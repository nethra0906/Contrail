"""M3 baselines (master spec §7: "MUST implement both and report"):
(a) historical mean delay by (airport, hour, day-of-week), and (b) LightGBM
on each node's own flat features plus its graph-neighbors' current delay -
what the GNN's extra propagation machinery has to beat to be worth it.
"""

from __future__ import annotations

from dataclasses import dataclass

import lightgbm as lgb
import numpy as np
import pandas as pd

from ml.datasets.network import FEATURE_COLUMNS, HORIZONS_BUCKETS


@dataclass
class HistoricalMeanBaseline:
    """Mean arrival delay observed in TRAINING data for each
    (airport_idx, hour_of_day, day_of_week) - looked up by the target
    bucket's own calendar fields, never by anything from the test fold
    (computing this from test data would leak the exact thing being
    predicted).
    """

    lookup: dict[tuple[int, int, int], float]
    global_mean: float

    def predict(self, airport_idx: int, hour: int, dow: int) -> float:
        return self.lookup.get((airport_idx, hour, dow), self.global_mean)


def fit_historical_mean_baseline(
    X_train: np.ndarray,
    y_train: np.ndarray,
    y_mask_train: np.ndarray,
    bucket_starts_train: np.ndarray,
) -> HistoricalMeanBaseline:
    """X_train: (T, N, F). y_train/y_mask_train: (T, N, H). Fits on the
    q50-equivalent target directly (arrival delay at horizon 0 == the
    node's own mean_arr_delay_min feature in later buckets), using every
    masked-in (bucket, node, horizon) observation pooled together - the
    baseline predicts the SAME number regardless of horizon (a pure
    calendar-based average has no notion of "further out"), which is
    exactly why it's a baseline.
    """
    hours = pd.DatetimeIndex(bucket_starts_train).hour.to_numpy()
    dows = pd.DatetimeIndex(bucket_starts_train).dayofweek.to_numpy()

    sums: dict[tuple[int, int, int], float] = {}
    counts: dict[tuple[int, int, int], int] = {}
    all_vals = []

    for h_i in range(len(HORIZONS_BUCKETS)):
        mask = y_mask_train[:, :, h_i]
        t_idx, n_idx = np.nonzero(mask)
        vals = y_train[t_idx, n_idx, h_i]
        for t, n, v in zip(t_idx, n_idx, vals, strict=True):
            key = (int(n), int(hours[t]), int(dows[t]))
            sums[key] = sums.get(key, 0.0) + float(v)
            counts[key] = counts.get(key, 0) + 1
        all_vals.extend(vals.tolist())

    lookup = {k: sums[k] / counts[k] for k in sums}
    global_mean = float(np.mean(all_vals)) if all_vals else 0.0
    return HistoricalMeanBaseline(lookup=lookup, global_mean=global_mean)


def predict_historical_mean(
    baseline: HistoricalMeanBaseline, bucket_starts: np.ndarray, n_nodes: int
) -> np.ndarray:
    """Returns (T, N, H) predictions - the same per-(node, hour, dow) mean
    repeated across every horizon, matching y's shape for direct comparison.
    """
    hours = pd.DatetimeIndex(bucket_starts).hour.to_numpy()
    dows = pd.DatetimeIndex(bucket_starts).dayofweek.to_numpy()
    t = len(bucket_starts)
    out = np.zeros((t, n_nodes, len(HORIZONS_BUCKETS)), dtype=np.float32)
    for ti in range(t):
        for n in range(n_nodes):
            out[ti, n, :] = baseline.predict(n, int(hours[ti]), int(dows[ti]))
    return out


def _neighbor_delay(X: np.ndarray, flow_adj: np.ndarray, t: int) -> np.ndarray:
    """(N,) - the flow-graph-weighted average of every other node's CURRENT
    mean_arr_delay_min at bucket t - the one piece of network information
    this baseline gets, everything else is purely local.
    """
    arr_delay_idx = FEATURE_COLUMNS.index("mean_arr_delay_min")
    return flow_adj @ X[t, :, arr_delay_idx]


def build_lgbm_tables(
    X: np.ndarray, y: np.ndarray, y_mask: np.ndarray, flow_adj: np.ndarray
) -> dict[int, pd.DataFrame]:
    """One flat table per horizon: each row is one (bucket, node) with that
    node's own current features plus its flow-neighbors' current delay,
    and the target at that horizon. Only masked-in rows are included.
    """
    t_steps, n_nodes, _ = X.shape
    tables: dict[int, pd.DataFrame] = {}
    neighbor_delay_by_t = np.stack([_neighbor_delay(X, flow_adj, t) for t in range(t_steps)])

    for h_i in range(len(HORIZONS_BUCKETS)):
        rows = []
        mask = y_mask[:, :, h_i]
        t_idx, n_idx = np.nonzero(mask)
        for t, n in zip(t_idx, n_idx, strict=True):
            row = {col: X[t, n, c] for c, col in enumerate(FEATURE_COLUMNS)}
            row["neighbor_delay"] = neighbor_delay_by_t[t, n]
            row["target"] = y[t, n, h_i]
            rows.append(row)
        tables[HORIZONS_BUCKETS[h_i]] = pd.DataFrame(rows)
    return tables


def train_lgbm_baseline(train_tables: dict[int, pd.DataFrame]) -> dict[int, lgb.LGBMRegressor]:
    """One LightGBM model per horizon - the flat-features-plus-neighbor-
    delay baseline the master spec asks for ("LightGBM on flat features
    INCLUDING neighbor delays").
    """
    models = {}
    feature_cols = [*FEATURE_COLUMNS, "neighbor_delay"]
    for horizon, table in train_tables.items():
        if len(table) < 50:
            continue
        model = lgb.LGBMRegressor(objective="mae", n_estimators=200, learning_rate=0.05, verbose=-1)
        model.fit(table[feature_cols], table["target"])
        models[horizon] = model
    return models


def predict_lgbm_baseline(
    models: dict[int, lgb.LGBMRegressor], test_tables: dict[int, pd.DataFrame]
) -> dict[int, np.ndarray]:
    feature_cols = [*FEATURE_COLUMNS, "neighbor_delay"]
    preds: dict[int, np.ndarray] = {}
    for horizon, model in models.items():
        table = test_tables.get(horizon)
        if table is None or len(table) == 0:
            continue
        preds[horizon] = np.asarray(model.predict(table[feature_cols]))
    return preds
