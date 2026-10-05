"""Trains M3 (delay-propagation GNN): historical-mean and LightGBM baselines
(master spec §7: "MUST implement both and report"), plus the diffusion-GCN +
temporal-encoder model, evaluated on a strict chronological split (never
random - execution rule 4). `make train` runs this after train_eta. Produces
a versioned model artifact under data/models/ and a metrics JSON that
ml/eval/report.py turns into docs/ml-report.md's M3 section.

Per docs/adr/0004: trains on one BTS month (January 2024, the same month
already cached for M2), not the master spec's "minimum 3 years" - the same
precedent M2 set, applied here for the same reason.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import torch

from ml.data.loaders.bts import load_month
from ml.datasets.network import HORIZONS_BUCKETS, SEQUENCE_LENGTH, build_arrays, build_graph
from ml.eval.network_baselines import (
    build_lgbm_tables,
    fit_historical_mean_baseline,
    predict_historical_mean,
    predict_lgbm_baseline,
    train_lgbm_baseline,
)
from ml.eval.report import update_report
from ml.export.register import register_and_maybe_promote_sync
from ml.models.delay_gnn import DelayGNN
from services.common.telemetry import configure_logging, get_logger

logger = get_logger(__name__)

MODEL_DIR = Path("data/models")
EPOCHS = 20
LEARNING_RATE = 1e-3
EARLY_STOP_PATIENCE = 4
TRAIN_FRAC, VAL_FRAC = 0.70, 0.15


def _time_split(total_buckets: int) -> tuple[slice, slice, slice, int, int]:
    """Chronological split by TIME BUCKET INDEX, never random - the
    numpy-array analogue of ml/data/split.py's chronological_split (that
    function operates on a DataFrame's date column; these are already a
    dense, evenly-spaced time axis, so splitting by index position is
    equivalent to splitting by time and avoids an unnecessary DataFrame
    round trip for a dense grid this size).
    """
    train_end = int(total_buckets * TRAIN_FRAC)
    val_end = int(total_buckets * (TRAIN_FRAC + VAL_FRAC))
    return (
        slice(0, train_end),
        slice(train_end, val_end),
        slice(val_end, total_buckets),
        train_end,
        val_end,
    )


def _make_sequences(
    X: np.ndarray, y: np.ndarray, y_mask: np.ndarray, rng: slice
) -> tuple[np.ndarray, ...]:
    """Builds (num_sequences, SEQUENCE_LENGTH, N, F) input windows and their
    aligned targets from a contiguous time range - a sequence's target is
    the y/y_mask at the LAST timestep of its window (the GNN forecasts
    forward from "now" = the window's end, same convention as M1).
    """
    start, stop = rng.start, rng.stop
    xs, ys, masks = [], [], []
    for t in range(start + SEQUENCE_LENGTH - 1, stop):
        xs.append(X[t - SEQUENCE_LENGTH + 1 : t + 1])
        ys.append(y[t])
        masks.append(y_mask[t])
    return np.stack(xs), np.stack(ys), np.stack(masks)


def _train_gnn(
    model: DelayGNN,
    flow_adj: torch.Tensor,
    rotation_adj: torch.Tensor,
    X_train: np.ndarray,
    y_train: np.ndarray,
    mask_train: np.ndarray,
    X_val: np.ndarray,
    y_val: np.ndarray,
    mask_val: np.ndarray,
) -> DelayGNN:
    torch.manual_seed(42)
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    X_train_t = torch.tensor(X_train, dtype=torch.float32)
    y_train_t = torch.tensor(y_train, dtype=torch.float32)
    mask_train_t = torch.tensor(mask_train, dtype=torch.bool)
    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.float32)
    mask_val_t = torch.tensor(mask_val, dtype=torch.bool)

    def masked_mae(pred: torch.Tensor, target: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        diff = (pred - target).abs()
        mask_f = mask.float()
        denom = mask_f.sum()
        if denom.item() == 0:
            return diff.sum() * 0.0
        return (diff * mask_f).sum() / denom

    best_val, best_state, bad_epochs = float("inf"), None, 0
    batch_size = 16
    n = X_train_t.shape[0]

    for epoch in range(EPOCHS):
        model.train()
        perm = torch.randperm(n)
        total_loss = 0.0
        for i in range(0, n, batch_size):
            idx = perm[i : i + batch_size]
            optimizer.zero_grad()
            pred = model(X_train_t[idx], flow_adj, rotation_adj)
            loss = masked_mae(pred, y_train_t[idx], mask_train_t[idx])
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(idx)
        train_loss = total_loss / max(n, 1)

        model.eval()
        with torch.no_grad():
            val_pred = model(X_val_t, flow_adj, rotation_adj)
            val_loss = masked_mae(val_pred, y_val_t, mask_val_t).item()

        logger.info("network_epoch", epoch=epoch, train_loss=train_loss, val_loss=val_loss)
        if val_loss < best_val:
            best_val = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
            if bad_epochs >= EARLY_STOP_PATIENCE:
                logger.info("network_early_stop", epoch=epoch, best_val_loss=best_val)
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model


def _mae_rmse_per_horizon(pred: np.ndarray, y: np.ndarray, mask: np.ndarray) -> dict:
    out: dict[int, dict] = {}
    for h_i, horizon in enumerate(HORIZONS_BUCKETS):
        m = mask[:, :, h_i]
        if not m.any():
            out[horizon // 4] = {"mae_min": None, "rmse_min": None, "n": 0}
            continue
        diff = pred[:, :, h_i][m] - y[:, :, h_i][m]
        out[horizon // 4] = {
            "mae_min": float(np.abs(diff).mean()),
            "rmse_min": float(np.sqrt((diff**2).mean())),
            "n": int(m.sum()),
        }
    return out


def train(bts_month: tuple[int, int] = (2024, 1)) -> dict:
    logger.info("network_training_start", month=bts_month)

    df = load_month(*bts_month)
    graph = build_graph(df)
    arrays = build_arrays(df, graph)
    n_nodes = len(graph.airports)
    logger.info(
        "network_graph_built",
        nodes=n_nodes,
        total_buckets=arrays.X.shape[0],
        mask_coverage=arrays.y_mask.mean(axis=(0, 1)).tolist(),
    )

    train_rng, val_rng, test_rng, train_end, val_end = _time_split(arrays.X.shape[0])
    X_train_seq, y_train_seq, mask_train_seq = _make_sequences(
        arrays.X, arrays.y, arrays.y_mask, train_rng
    )
    X_val_seq, y_val_seq, mask_val_seq = _make_sequences(arrays.X, arrays.y, arrays.y_mask, val_rng)
    X_test_seq, y_test_seq, mask_test_seq = _make_sequences(
        arrays.X, arrays.y, arrays.y_mask, test_rng
    )
    logger.info(
        "network_split",
        train_sequences=len(X_train_seq),
        val_sequences=len(X_val_seq),
        test_sequences=len(X_test_seq),
    )

    # --- Baseline (a): historical mean by (airport, hour, dow) ---
    hist_baseline = fit_historical_mean_baseline(
        arrays.X[train_rng],
        arrays.y[train_rng],
        arrays.y_mask[train_rng],
        arrays.bucket_starts[train_rng],
    )
    test_bucket_starts = arrays.bucket_starts[test_rng][SEQUENCE_LENGTH - 1 :]
    hist_pred = predict_historical_mean(hist_baseline, test_bucket_starts, n_nodes)
    hist_metrics = _mae_rmse_per_horizon(hist_pred, y_test_seq, mask_test_seq)

    # --- Baseline (b): LightGBM on flat features + neighbor delay ---
    train_tables = build_lgbm_tables(
        arrays.X[train_rng], arrays.y[train_rng], arrays.y_mask[train_rng], graph.flow_adj
    )
    test_tables = build_lgbm_tables(
        arrays.X[test_rng], arrays.y[test_rng], arrays.y_mask[test_rng], graph.flow_adj
    )
    lgbm_models = train_lgbm_baseline(train_tables)
    lgbm_test_preds = predict_lgbm_baseline(lgbm_models, test_tables)
    lgbm_metrics: dict[int, dict] = {}
    for horizon in HORIZONS_BUCKETS:
        table = test_tables.get(horizon)
        pred = lgbm_test_preds.get(horizon)
        if table is None or pred is None or len(table) == 0:
            lgbm_metrics[horizon // 4] = {"mae_min": None, "rmse_min": None, "n": 0}
            continue
        diff = pred - table["target"].to_numpy()
        lgbm_metrics[horizon // 4] = {
            "mae_min": float(np.abs(diff).mean()),
            "rmse_min": float(np.sqrt((diff**2).mean())),
            "n": int(len(table)),
        }

    # --- Model: diffusion GCN + temporal GRU ---
    model = DelayGNN()
    flow_adj_t = torch.tensor(graph.flow_adj, dtype=torch.float32)
    rotation_adj_t = torch.tensor(graph.rotation_adj, dtype=torch.float32)
    model = _train_gnn(
        model,
        flow_adj_t,
        rotation_adj_t,
        X_train_seq,
        y_train_seq,
        mask_train_seq,
        X_val_seq,
        y_val_seq,
        mask_val_seq,
    )

    model.eval()
    with torch.no_grad():
        test_pred = model(
            torch.tensor(X_test_seq, dtype=torch.float32), flow_adj_t, rotation_adj_t
        ).numpy()
    gnn_metrics = _mae_rmse_per_horizon(test_pred, y_test_seq, mask_test_seq)

    gnn_maes = [m["mae_min"] for m in gnn_metrics.values() if m["mae_min"] is not None]
    overall_mae = float(np.mean(gnn_maes)) if gnn_maes else float("nan")

    for h_hours in sorted(gnn_metrics):
        logger.info(
            "network_horizon_result",
            horizon_h=h_hours,
            gnn_mae=gnn_metrics[h_hours]["mae_min"],
            historical_mean_mae=hist_metrics[h_hours]["mae_min"],
            lgbm_mae=lgbm_metrics[h_hours]["mae_min"],
        )

    version = f"delay-gnn-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / f"{version}.pt"
    torch.save(
        {
            "state_dict": model.state_dict(),
            "airports": graph.airports,
            "flow_adj": graph.flow_adj,
            "rotation_adj": graph.rotation_adj,
        },
        model_path,
    )

    report = {
        "kind": "network",
        "model_version": version,
        "trained_at": dt.datetime.now(dt.UTC).isoformat(),
        "train_window": {
            "source": f"BTS {bts_month[0]}-{bts_month[1]:02d} (docs/adr/0004)",
            "nodes": n_nodes,
            "train_sequences": int(len(X_train_seq)),
            "val_sequences": int(len(X_val_seq)),
            "test_sequences": int(len(X_test_seq)),
        },
        "historical_mean_baseline": {"per_horizon_hours": hist_metrics},
        "lgbm_baseline": {"per_horizon_hours": lgbm_metrics},
        "model": {"per_horizon_hours": gnn_metrics, "overall_mae_min": overall_mae},
        "artifact_path": str(model_path),
    }
    metrics_path = MODEL_DIR / f"{version}.metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2, default=str))

    update_report(report)
    register_and_maybe_promote_sync(report)

    logger.info("network_training_complete", version=version, artifact=str(model_path))
    return report


if __name__ == "__main__":
    configure_logging()
    train()
