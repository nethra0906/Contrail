"""Train/serve feature parity test for M2 (one of the two tests the master
spec calls non-negotiable before a stage is "done" - see §11, alongside the
simulator's determinism golden test).

There is no live-serving ETA endpoint yet (services/inference/eta.py isn't
built - see services/common/features/eta.py's module docstring), so this
can't yet compare "training's computed features" against "a live request's
computed features" literally. What it CAN and does guard against: the
dataset builder (ml/datasets/eta.py) calling the single shared feature
function (compute_eta_features) correctly, rather than a hand-rolled
vectorized shortcut that happens to usually agree with it. That's the
failure mode this test catches - someone "optimizing" build_feature_table
with inline pandas arithmetic that quietly drifts from
compute_eta_features's actual logic. When the live serving path is built,
extend this test to call it directly instead of re-deriving inputs by hand.
"""

from __future__ import annotations

import pandas as pd

from ml.datasets.eta import TaxiInLookup, add_dest_congestion, build_feature_table
from services.common.features.eta import FEATURE_COLUMNS, compute_eta_features


def _raw_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "flight_date": pd.Timestamp("2024-01-08"),
                "carrier": "9E",
                "origin": "LGA",
                "dest": "OMA",
                "crs_dep_hhmm": "0856",
                "dep_delay_min": -5.0,
                "crs_arr_hhmm": "1135",
                "arr_delay_min": -11.0,
                "sched_elapsed_min": 219.0,
                "taxi_in_min": 4.0,
                "distance_mi": 1148.0,
                "day_of_week": 1,
                "month": 1,
                "cancelled": 0.0,
                "diverted": 0.0,
            },
            {
                "flight_date": pd.Timestamp("2024-01-08"),
                "carrier": "9E",
                "origin": "JFK",
                "dest": "OMA",
                "crs_dep_hhmm": "1120",
                "dep_delay_min": 10.0,
                "crs_arr_hhmm": "1150",
                "arr_delay_min": 5.0,
                "sched_elapsed_min": 230.0,
                "taxi_in_min": 8.0,
                "distance_mi": 1160.0,
                "day_of_week": 1,
                "month": 1,
                "cancelled": 0.0,
                "diverted": 0.0,
            },
            {
                "flight_date": pd.Timestamp("2024-01-09"),
                "carrier": "WN",
                "origin": "LAX",
                "dest": "OMA",
                "crs_dep_hhmm": "0700",
                "dep_delay_min": 0.0,
                "crs_arr_hhmm": "1230",
                "arr_delay_min": -2.0,
                "sched_elapsed_min": 210.0,
                "taxi_in_min": 6.0,
                "distance_mi": 1300.0,
                "day_of_week": 2,
                "month": 1,
                "cancelled": 0.0,
                "diverted": 0.0,
            },
        ]
    )


def test_build_feature_table_matches_calling_compute_eta_features_directly():
    raw = _raw_rows()
    taxi_in = TaxiInLookup(raw)  # same fold used as both "train" and reference here

    table = build_feature_table(raw, taxi_in)

    # Independently recompute dest_sched_arrivals_30min the same way the
    # dataset builder is documented to (same-day, same destination, same
    # 30-minute scheduled-arrival block) and feed it straight into the
    # single shared function - this is the "serving" side of the parity
    # check, computed without reusing build_feature_table's internals.
    congested = add_dest_congestion(raw)
    for i, row in congested.iterrows():
        expected = compute_eta_features(
            distance_mi=row.distance_mi,
            sched_elapsed_min=row.sched_elapsed_min,
            dep_delay_min=row.dep_delay_min,
            sched_dep_hhmm=row.crs_dep_hhmm,
            day_of_week=row.day_of_week,
            month=row.month,
            carrier=row.carrier,
            origin=row.origin,
            dest=row.dest,
            dest_sched_arrivals_30min=row.dest_sched_arrivals_30min,
            historic_taxi_in_p50_min=taxi_in.lookup(row.dest, row.carrier),
        )
        for col in FEATURE_COLUMNS:
            assert table.loc[i, col] == getattr(expected, col), (
                f"row {i} column {col!r}: dataset builder diverged from compute_eta_features"
            )
