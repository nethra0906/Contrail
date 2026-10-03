"""Turns a loaded BTS month (ml/data/loaders/bts.py) into M2's training
table, applying `services/common/features/eta.py`'s single feature
implementation row-by-row - the same function a live server would call, so
there is one feature computation, not a training-time copy that can drift
from a serving-time copy (execution rule 5).
"""

from __future__ import annotations

import pandas as pd

from services.common.features.eta import FEATURE_COLUMNS, compute_eta_features

TARGET_COLUMN = "arr_delay_min"


def exclude_non_arrivals(df: pd.DataFrame) -> pd.DataFrame:
    """Cancelled/diverted flights have no real arrival delay to train
    against - dropped rather than imputed, per the project's standing rule
    against fabricating data for genuinely missing outcomes.
    """
    mask = (df["cancelled"] == 0) & (df["diverted"] == 0) & df["arr_delay_min"].notna()
    return df.loc[mask].copy()


def _minutes_since_midnight(hhmm: str | int) -> float:
    v = int(hhmm)
    return (v // 100) * 60 + (v % 100)


def add_dest_congestion(df: pd.DataFrame) -> pd.DataFrame:
    """Count of OTHER scheduled arrivals at the same destination within the
    same 30-minute scheduled block, on the same day - derived purely from
    the published timetable (CRS arrival time), so it's known before
    operation and isn't a leak of any flight's actual outcome.
    """
    df = df.copy()
    arr_block = df["crs_arr_hhmm"].map(_minutes_since_midnight) // 30
    group_size = df.groupby(["flight_date", "dest", arr_block])["dest"].transform("count")
    df["dest_sched_arrivals_30min"] = (group_size - 1).astype(int)
    return df


class TaxiInLookup:
    """Historic median taxi-in minutes for (dest, carrier), built from ONE
    fold only (the training fold) and then applied read-only to every split
    - computing this from val/test rows would leak future information about
    those airports' operations into the features.
    """

    def __init__(self, train_df: pd.DataFrame) -> None:
        self._pair = train_df.groupby(["dest", "carrier"], observed=True)["taxi_in_min"].median()
        self._dest = train_df.groupby("dest", observed=True)["taxi_in_min"].median()
        global_median = train_df["taxi_in_min"].median()
        self._global = float(global_median) if pd.notna(global_median) else None

    def lookup(self, dest: str, carrier: str) -> float | None:
        pair_val = self._pair.get((dest, carrier))
        if pair_val is not None and pd.notna(pair_val):
            return float(pair_val)
        dest_val = self._dest.get(dest)
        if dest_val is not None and pd.notna(dest_val):
            return float(dest_val)
        return self._global


def build_feature_table(df: pd.DataFrame, taxi_in: TaxiInLookup) -> pd.DataFrame:
    """Applies compute_eta_features to every row. Returns a DataFrame with
    FEATURE_COLUMNS plus TARGET_COLUMN, ready for the model (baseline or
    LightGBM) - both train on exactly this table's columns.
    """
    df = add_dest_congestion(df)

    def _row(r: pd.Series) -> pd.Series:
        f = compute_eta_features(
            distance_mi=r.distance_mi,
            sched_elapsed_min=r.sched_elapsed_min,
            dep_delay_min=r.dep_delay_min,
            sched_dep_hhmm=r.crs_dep_hhmm,
            day_of_week=r.day_of_week,
            month=r.month,
            carrier=r.carrier,
            origin=r.origin,
            dest=r.dest,
            dest_sched_arrivals_30min=r.dest_sched_arrivals_30min,
            historic_taxi_in_p50_min=taxi_in.lookup(r.dest, r.carrier),
        )
        return pd.Series(
            {col: getattr(f, col) for col in FEATURE_COLUMNS} | {TARGET_COLUMN: r.arr_delay_min}
        )

    table = df.apply(_row, axis=1)
    for col in ["carrier", "origin", "dest"]:
        table[col] = table[col].astype("category")
    return table
