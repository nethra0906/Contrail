"""Builds M3's airport graph (flow + rotation edges) and the per-airport,
per-15-minute-bucket node feature/target tensors the delay-propagation GNN
trains on, from a loaded BTS month (ml/data/loaders/bts.py).

Per docs/adr/0004: trains on ONE BTS month (January 2024, already cached for
M2), not the master spec's "minimum 3 years" - the same documented,
precedent-setting scope reduction M2 made, for the same reason (a project at
this scale reporting honest real numbers on a smaller window beats claiming
a volume of data it doesn't actually have). The chronological split still
has real temporal structure within that month (31 days, split by date).

Applies services/common/features/network.py's single feature implementation
per bucket - the same function a live-serving caller
(`/airports/{icao}/delay-forecast`) would use, per execution rule 5.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from services.common.features.network import FEATURE_COLUMNS, compute_network_features

BUCKET_MINUTES = 15
BUCKETS_PER_HOUR = 60 // BUCKET_MINUTES

# Prediction horizons in 15-min buckets: 1h, 2h, 3h, 4h, 5h, 6h (master spec §7: t+1h..t+6h).
HORIZONS_BUCKETS: tuple[int, ...] = tuple(h * BUCKETS_PER_HOUR for h in (1, 2, 3, 4, 5, 6))

# Trailing history (in buckets) the temporal encoder conditions on - 2 hours.
SEQUENCE_LENGTH = 8

NUM_NODE_FEATURES = len(FEATURE_COLUMNS)


@dataclass
class AirportGraph:
    airports: list[str]  # node order; index into this list IS the node id used everywhere below
    flow_adj: np.ndarray  # (N, N) float32, row-normalized scheduled-flight-volume edges
    rotation_adj: np.ndarray  # (N, N) float32, row-normalized aircraft-rotation edges


@dataclass
class NetworkArrays:
    graph: AirportGraph
    # X: (T, N, NUM_NODE_FEATURES) - one row per (time bucket, airport).
    X: np.ndarray
    bucket_starts: np.ndarray  # (T,) datetime64[ns] - the real-world start time of each bucket row
    # y/y_mask: (T, N, len(HORIZONS_BUCKETS)) - mean arrival delay (minutes) at
    # each horizon, and whether that future bucket exists in this window.
    y: np.ndarray
    y_mask: np.ndarray


def _hhmm_to_minutes(hhmm) -> float:
    v = int(hhmm) if pd.notna(hhmm) else 0
    return (v // 100) * 60 + (v % 100)


def _bucket_index_series(
    flight_date: pd.Series, hhmm: pd.Series, bucket_origin: pd.Timestamp
) -> pd.Series:
    """Vectorized form of "which 15-min bucket does this HHMM scheduled time
    on this date fall into" - a per-row Python loop over hundreds of
    thousands of BTS rows measured as a real bottleneck building
    ml/datasets/trajectory.py's windows earlier in this project; this stays
    in pandas/numpy throughout rather than repeating that mistake.
    """
    minutes = hhmm.fillna(0).astype(int)
    minutes_since_midnight = (minutes // 100) * 60 + (minutes % 100)
    ts = flight_date + pd.to_timedelta(minutes_since_midnight, unit="m")
    return ((ts - bucket_origin).dt.total_seconds() // (BUCKET_MINUTES * 60)).astype(int)


def build_graph(df: pd.DataFrame) -> AirportGraph:
    """Flow edges: symmetrized, row-normalized scheduled-flight-count
    between every airport pair. Rotation edges: for the same tail number,
    one leg's destination -> the next leg's origin (chronologically) - this
    project's historical stand-in for `flights.prev_leg_flight_id` (the
    live DB field the spec names, which isn't populated by the live
    pipeline yet - rotation-chain linking across legs isn't built there).
    BTS's own tail_number field gives the identical signal historically,
    with no dependency on that live feature existing.
    """
    airports = sorted(set(df["origin"]).union(df["dest"]))
    idx = {a: i for i, a in enumerate(airports)}
    n = len(airports)

    flow = np.zeros((n, n), dtype=np.float32)
    for (o, d), count in df.groupby(["origin", "dest"], observed=True).size().items():
        flow[idx[o], idx[d]] += count
        flow[idx[d], idx[o]] += count

    rotation = np.zeros((n, n), dtype=np.float32)
    with_tail = df.dropna(subset=["tail_number"]).copy()
    with_tail["dep_minutes"] = with_tail["crs_dep_hhmm"].map(_hhmm_to_minutes)
    with_tail = with_tail.sort_values(["tail_number", "flight_date", "dep_minutes"])
    for _, legs in with_tail.groupby("tail_number", observed=True):
        legs = legs.reset_index(drop=True)
        for i in range(len(legs) - 1):
            cur_dest, next_origin = legs.loc[i, "dest"], legs.loc[i + 1, "origin"]
            if cur_dest == next_origin:
                rotation[idx[cur_dest], idx[legs.loc[i + 1, "dest"]]] += 1

    def _row_normalize(m: np.ndarray) -> np.ndarray:
        row_sums = m.sum(axis=1, keepdims=True)
        return np.divide(m, row_sums, out=np.zeros_like(m), where=row_sums > 0)

    return AirportGraph(
        airports=airports, flow_adj=_row_normalize(flow), rotation_adj=_row_normalize(rotation)
    )


def build_arrays(df: pd.DataFrame, graph: AirportGraph) -> NetworkArrays:
    """Bins every flight into a 15-min departure bucket (by origin) and
    arrival bucket (by dest), aggregates per (airport, bucket), and fills a
    dense (T, N, features) grid - including buckets with zero operations,
    since the GNN needs a consistent time axis across every node, not a
    sparse "only when something happened" table.
    """
    df = df.copy()
    df["flight_date"] = pd.to_datetime(df["flight_date"])
    bucket_origin = df["flight_date"].min()
    last_date = df["flight_date"].max()
    total_buckets = int(((last_date - bucket_origin).days + 1) * 24 * BUCKETS_PER_HOUR)

    df["dep_bucket"] = _bucket_index_series(df["flight_date"], df["crs_dep_hhmm"], bucket_origin)
    df["arr_bucket"] = _bucket_index_series(df["flight_date"], df["crs_arr_hhmm"], bucket_origin)

    dep_agg = (
        df.groupby(["origin", "dep_bucket"], observed=True)
        .agg(
            mean_dep_delay_min=("dep_delay_min", "mean"),
            dep_count=("dep_delay_min", "size"),
            cancellations=("cancelled", "sum"),
        )
        .reset_index()
        .rename(columns={"origin": "airport", "dep_bucket": "bucket"})
    )
    arr_agg = (
        df.groupby(["dest", "arr_bucket"], observed=True)
        .agg(mean_arr_delay_min=("arr_delay_min", "mean"), arr_count=("arr_delay_min", "size"))
        .reset_index()
        .rename(columns={"dest": "airport", "arr_bucket": "bucket"})
    )
    merged = pd.merge(dep_agg, arr_agg, on=["airport", "bucket"], how="outer")

    n = len(graph.airports)
    idx = {a: i for i, a in enumerate(graph.airports)}
    X = np.zeros((total_buckets, n, NUM_NODE_FEATURES), dtype=np.float32)

    for row in merged.itertuples():
        if row.airport not in idx or not (0 <= row.bucket < total_buckets):
            continue
        bucket_ts = bucket_origin + pd.Timedelta(minutes=row.bucket * BUCKET_MINUTES)
        features = compute_network_features(
            mean_dep_delay_min=None
            if pd.isna(row.mean_dep_delay_min)
            else float(row.mean_dep_delay_min),
            mean_arr_delay_min=None
            if pd.isna(row.mean_arr_delay_min)
            else float(row.mean_arr_delay_min),
            ops_count=int(
                (row.dep_count if pd.notna(row.dep_count) else 0)
                + (row.arr_count if pd.notna(row.arr_count) else 0)
            ),
            cancellations=int(row.cancellations) if pd.notna(row.cancellations) else 0,
            bucket_hour=bucket_ts.hour,
            bucket_day_of_week=bucket_ts.dayofweek,
            bucket_date_iso=bucket_ts.strftime("%Y-%m-%d"),
        )
        X[row.bucket, idx[row.airport]] = [getattr(features, c) for c in FEATURE_COLUMNS]

    bucket_starts = np.array(
        [bucket_origin + pd.Timedelta(minutes=b * BUCKET_MINUTES) for b in range(total_buckets)],
        dtype="datetime64[ns]",
    )

    arr_delay_idx = FEATURE_COLUMNS.index("mean_arr_delay_min")
    y = np.zeros((total_buckets, n, len(HORIZONS_BUCKETS)), dtype=np.float32)
    y_mask = np.zeros((total_buckets, n, len(HORIZONS_BUCKETS)), dtype=bool)
    ops_idx = FEATURE_COLUMNS.index("ops_count")
    for h_i, h in enumerate(HORIZONS_BUCKETS):
        valid = total_buckets - h
        if valid <= 0:
            continue
        y[:valid, :, h_i] = X[h : h + valid, :, arr_delay_idx]
        # Only a real target when the future bucket actually had operations -
        # a zero-ops bucket's "0.0 mean delay" is a filler value, not a
        # genuine "no delay" observation (see compute_network_features'
        # docstring), so it must not silently count as ground truth.
        y_mask[:valid, :, h_i] = X[h : h + valid, :, ops_idx] > 0

    return NetworkArrays(graph=graph, X=X, bucket_starts=bucket_starts, y=y, y_mask=y_mask)
