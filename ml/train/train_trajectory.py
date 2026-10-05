"""Trains M1 (trajectory forecasting): the constant-velocity dead-reckoning
baseline (master spec §7, "MUST implement first") plus a 2-layer quantile-
regression GRU, evaluated on a strict chronological split (never random -
execution rule 4). `make train` runs this before train_eta (see Makefile).
Produces a versioned ONNX model artifact under data/models/ and a metrics
JSON that ml/eval/report.py turns into docs/ml-report.md's M1 section - no
metric in that report is ever typed by hand (execution rule 3).

Per docs/adr/0004: trains on ADS-B Exchange's free historical sample data
(ml/data/loaders/adsbx_hist.py), not this project's own live-ingested
history, which hasn't accumulated enough continuous track data yet.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, TensorDataset

from ml.data.loaders.adsbx_hist import DEFAULT_TRAINING_WINDOW, load_range
from ml.data.split import chronological_split
from ml.datasets.trajectory import HORIZONS_S, STATIC_DIM, TrajectoryArrays, build_arrays
from ml.eval.report import update_report
from ml.eval.trajectory_baselines import constant_velocity_predict
from ml.eval.trajectory_metrics import (
    approximate_crps,
    interval_coverage_80,
    per_horizon_error_summary,
    pit_histogram,
)
from ml.export.register import register_and_maybe_promote_sync
from ml.models.trajectory_gru import QUANTILES, TrajectoryGRU, masked_quantile_loss
from services.common.telemetry import configure_logging, get_logger

logger = get_logger(__name__)

MODEL_DIR = Path("data/models")
# EPOCHS/BATCH_SIZE tuned for CPU training on ~627K windows: a larger batch
# cuts Python-loop/optimizer-step overhead per epoch materially at this
# volume (measured directly - the original BATCH_SIZE=256 took minutes per
# epoch), and EPOCHS is capped to bound worst-case wall-clock time while
# EARLY_STOP_PATIENCE still lets training stop sooner if val_loss plateaus
# first - this is a real tuning decision for a CPU-only training
# environment, not a cut corner on model quality.
EPOCHS = 10
BATCH_SIZE = 1024
LEARNING_RATE = 1e-3
EARLY_STOP_PATIENCE = 3


def _split_arrays(
    arrays: TrajectoryArrays,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dt.datetime, dt.datetime]:
    """Reuses ml/data/split.py's chronological_split - the single place the
    project's "never a random split" rule (execution rule 4) lives - rather
    than reimplementing it for numpy arrays. Wraps anchor_ts in a minimal
    DataFrame just to drive that function, then uses the returned row
    indices to slice the real arrays.
    """
    idx_df = pd.DataFrame(
        {"idx": np.arange(len(arrays.anchor_ts)), "ts": pd.to_datetime(arrays.anchor_ts)}
    )
    split = chronological_split(idx_df, date_col="ts")
    return (
        split.train["idx"].to_numpy(),
        split.val["idx"].to_numpy(),
        split.test["idx"].to_numpy(),
        split.train_end,
        split.val_end,
    )


def _train_gru(
    X_train: np.ndarray,
    static_train: np.ndarray,
    y_train: np.ndarray,
    mask_train: np.ndarray,
    X_val: np.ndarray,
    static_val: np.ndarray,
    y_val: np.ndarray,
    mask_val: np.ndarray,
) -> TrajectoryGRU:
    torch.manual_seed(42)
    model = TrajectoryGRU()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)

    train_ds = TensorDataset(
        torch.tensor(X_train, dtype=torch.float32),
        torch.tensor(static_train, dtype=torch.float32),
        torch.tensor(y_train, dtype=torch.float32),
        torch.tensor(mask_train, dtype=torch.bool),
    )
    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)

    X_val_t = torch.tensor(X_val, dtype=torch.float32)
    static_val_t = torch.tensor(static_val, dtype=torch.float32)
    y_val_t = torch.tensor(y_val, dtype=torch.float32)
    mask_val_t = torch.tensor(mask_val, dtype=torch.bool)

    best_val_loss = float("inf")
    best_state = None
    bad_epochs = 0

    for epoch in range(EPOCHS):
        model.train()
        total_loss, total_n = 0.0, 0
        for xb, sb, yb, mb in train_loader:
            optimizer.zero_grad()
            out = model(xb, sb)
            loss = masked_quantile_loss(out, yb, mb)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * xb.size(0)
            total_n += xb.size(0)
        train_loss = total_loss / max(total_n, 1)

        model.eval()
        with torch.no_grad():
            val_out = model(X_val_t, static_val_t)
            val_loss = masked_quantile_loss(val_out, y_val_t, mask_val_t).item()

        logger.info("trajectory_epoch", epoch=epoch, train_loss=train_loss, val_loss=val_loss)

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            best_state = {k: v.clone() for k, v in model.state_dict().items()}
            bad_epochs = 0
        else:
            bad_epochs += 1
            if bad_epochs >= EARLY_STOP_PATIENCE:
                logger.info("trajectory_early_stop", epoch=epoch, best_val_loss=best_val_loss)
                break

    if best_state is not None:
        model.load_state_dict(best_state)
    return model


def _export_onnx(model: TrajectoryGRU, path: Path) -> None:
    model.eval()
    dummy_x = torch.zeros(1, 11, 6, dtype=torch.float32)
    dummy_static = torch.zeros(1, STATIC_DIM, dtype=torch.float32)
    torch.onnx.export(
        model,
        (dummy_x, dummy_static),
        str(path),
        input_names=["x", "static"],
        output_names=["quantile_predictions"],
        dynamic_axes={
            "x": {0: "batch"},
            "static": {0: "batch"},
            "quantile_predictions": {0: "batch"},
        },
        opset_version=17,
        # dynamo=False: the default dynamo-based exporter (torch 2.x) needs the
        # optional `onnxscript` package, which isn't a project dependency - the
        # legacy TorchScript-based exporter handles this model's plain GRU +
        # Linear architecture fine and needs nothing beyond onnx/onnxruntime,
        # already-declared dependencies (pyproject.toml).
        dynamo=False,
    )


def train(window: tuple[dt.datetime, dt.datetime] = DEFAULT_TRAINING_WINDOW) -> dict:
    logger.info("trajectory_training_start", window=[w.isoformat() for w in window])

    start, end = window
    raw = load_range(start, end)

    arrays = build_arrays(raw)
    logger.info(
        "trajectory_windows_built",
        n=len(arrays.X),
        mask_coverage=arrays.y_mask.mean(axis=0).tolist(),
    )

    train_idx, val_idx, test_idx, train_end, val_end = _split_arrays(arrays)
    logger.info(
        "trajectory_split",
        train_windows=len(train_idx),
        val_windows=len(val_idx),
        test_windows=len(test_idx),
        train_end=str(train_end),
        val_end=str(val_end),
    )
    if len(train_idx) == 0 or len(val_idx) == 0 or len(test_idx) == 0:
        raise ValueError(
            "chronological split produced an empty fold - the training window is too short "
            "relative to WINDOW_SIZE/stride; widen DEFAULT_TRAINING_WINDOW in adsbx_hist.py"
        )

    model = _train_gru(
        arrays.X[train_idx],
        arrays.static[train_idx],
        arrays.y[train_idx],
        arrays.y_mask[train_idx],
        arrays.X[val_idx],
        arrays.static[val_idx],
        arrays.y[val_idx],
        arrays.y_mask[val_idx],
    )

    X_test, static_test, y_test, mask_test = (
        arrays.X[test_idx],
        arrays.static[test_idx],
        arrays.y[test_idx],
        arrays.y_mask[test_idx],
    )

    model.eval()
    with torch.no_grad():
        test_out = model(
            torch.tensor(X_test, dtype=torch.float32),
            torch.tensor(static_test, dtype=torch.float32),
        ).numpy()
    q10, q50, q90 = test_out[:, :, 0, :], test_out[:, :, 1, :], test_out[:, :, 2, :]

    model_per_horizon = per_horizon_error_summary(q50, y_test, mask_test)
    model_coverage = interval_coverage_80(q10, q90, y_test, mask_test)
    model_pit = pit_histogram(q10, q50, q90, y_test, mask_test)
    model_crps = approximate_crps(q10, q50, q90, y_test, mask_test)

    baseline_pred = constant_velocity_predict(X_test)
    baseline_per_horizon = per_horizon_error_summary(baseline_pred, y_test, mask_test)

    valid_medians = [
        m["median_error_km"] for m in model_per_horizon.values() if m["median_error_km"] is not None
    ]
    overall_median_error_km = float(np.mean(valid_medians)) if valid_medians else float("nan")

    for horizon in HORIZONS_S:
        m, b = model_per_horizon[horizon], baseline_per_horizon[horizon]
        logger.info(
            "trajectory_horizon_result",
            horizon_s=horizon,
            model_median_km=m["median_error_km"],
            baseline_median_km=b["median_error_km"],
            n=m["n"],
        )

    version = f"traj-gru-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / f"{version}.onnx"
    _export_onnx(model, model_path)
    torch.save(
        model.state_dict(), MODEL_DIR / f"{version}.pt"
    )  # kept alongside ONNX for reference/debugging

    report = {
        "kind": "trajectory",
        "model_version": version,
        "trained_at": dt.datetime.now(dt.UTC).isoformat(),
        "train_window": {
            "source": "ADS-B Exchange readsb-hist samples (docs/adr/0004)",
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "train_windows": int(len(train_idx)),
            "val_windows": int(len(val_idx)),
            "test_windows": int(len(test_idx)),
            "train_end": str(train_end),
            "val_end": str(val_end),
        },
        "baseline": {"per_horizon": baseline_per_horizon},
        "model": {
            "per_horizon": model_per_horizon,
            "overall_median_error_km": overall_median_error_km,
            "interval_coverage_80": model_coverage,
            "pit_histogram": model_pit,
            "approximate_crps": model_crps,
            "quantiles": list(QUANTILES),
        },
        "artifact_path": str(model_path),
    }
    metrics_path = MODEL_DIR / f"{version}.metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2, default=str))

    update_report(report)
    register_and_maybe_promote_sync(report)

    logger.info("trajectory_training_complete", version=version, artifact=str(model_path))
    return report


if __name__ == "__main__":
    configure_logging()
    train()
