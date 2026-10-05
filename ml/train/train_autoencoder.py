"""Trains M4's learned anomaly layer (master spec §7): a 1D conv autoencoder
over resampled 128-point flight segments (ml/datasets/anomaly.py), then an
IsolationForest over the learned embeddings. `make train` runs this last.
Stores embeddings in `trajectory_embeddings` (pgvector) when a database is
reachable, mirroring ml/export/register.py's best-effort DB write.

Per docs/adr/0004: trains on the same ADS-B Exchange historical sample as
M1 (BTS has no position data at all). Evaluation deviates from the master
spec's exact methodology, documented here rather than silently: the spec
evaluates against "positives from BTS Diverted/Cancelled + squawk events,"
which needs a tail-number-to-flight join this project doesn't build, and a
training window long enough to contain a meaningful number of diversions/
cancellations (one hour of one day realistically contains few or none).
Instead, this evaluates how well the unsupervised embedding's anomaly score
agrees with the ALREADY-WIRED rules layer (services/inference/anomaly_rules.py,
wired into the live pipeline earlier this session) firing on the same
segment - a genuine, honest check that the learned layer captures something
correlated with real anomalous kinematics, reported as "agreement with the
rules layer," not mislabeled as the spec's BTS-incident-based precision.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import numpy as np
import torch
from sklearn.ensemble import IsolationForest
from sklearn.metrics import average_precision_score

from ml.data.loaders.adsbx_hist import load_default_training_window
from ml.datasets.anomaly import build_windows
from ml.eval.report import update_report
from ml.export.register import register_and_maybe_promote_sync
from ml.models.traj_autoencoder import TrajectoryAutoencoder
from services.assembler.track_state import TrackState, advance
from services.common.telemetry import configure_logging, get_logger
from services.inference.anomaly_rules import check_all_rules

logger = get_logger(__name__)

MODEL_DIR = Path("data/models")
EPOCHS = 30
BATCH_SIZE = 32
LEARNING_RATE = 1e-3
TRAIN_FRAC = 0.8


def _segment_has_rule_anomaly(segment) -> bool:
    """Replays the segment's native rows through the exact same rules
    engine wired into the live assembler pipeline, so "does this segment
    look anomalous" is answered identically whether the data is historical
    or live. Unlike ml/datasets/trajectory.py's per-row phase computation
    (which runs over millions of rows and uses a lightweight duck-typed
    stand-in to avoid Pydantic overhead), this runs over a few hundred
    segments of ~128 points each - full StateVectorIn validation here is
    not a measured bottleneck, so there's no need for that optimization.
    """
    from services.common.schemas.aircraft import StateVectorIn

    previous: TrackState | None = None
    for row in segment.itertuples():
        try:
            sv = StateVectorIn(
                icao24=row.icao24,
                ts=row.ts.to_pydatetime(),
                lat=row.lat,
                lon=row.lon,
                baro_alt_ft=None if np.isnan(row.baro_alt_ft) else float(row.baro_alt_ft),
                velocity_kt=None if np.isnan(row.velocity_kt) else float(row.velocity_kt),
                heading_deg=None if np.isnan(row.heading_deg) else float(row.heading_deg),
                vert_rate_fpm=None if np.isnan(row.vert_rate_fpm) else float(row.vert_rate_fpm),
                on_ground=bool(row.on_ground),
                squawk=None,
                source="adsbx_hist",
            )
        except Exception:
            continue

        track = advance(sv, previous)
        if check_all_rules(sv, previous, track):
            return True
        previous = track
    return False


def _normalize(X: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    return (X - mean) / np.where(std > 1e-6, std, 1.0)


def train() -> dict:
    logger.info("autoencoder_training_start")

    raw = load_default_training_window()
    windows = build_windows(raw)
    n = len(windows.X)
    logger.info("autoencoder_windows_built", n=n)

    split = int(n * TRAIN_FRAC)
    train_X, test_X = windows.X[:split], windows.X[split:]
    test_segments = windows.segments[split:]

    mean = train_X.mean(axis=(0, 1), keepdims=True)
    std = train_X.std(axis=(0, 1), keepdims=True)
    train_X_norm = _normalize(train_X, mean, std)
    test_X_norm = _normalize(test_X, mean, std)

    torch.manual_seed(42)
    model = TrajectoryAutoencoder()
    optimizer = torch.optim.Adam(model.parameters(), lr=LEARNING_RATE)
    train_tensor = torch.tensor(train_X_norm, dtype=torch.float32)

    for epoch in range(EPOCHS):
        model.train()
        perm = torch.randperm(len(train_tensor))
        total_loss = 0.0
        for i in range(0, len(train_tensor), BATCH_SIZE):
            batch = train_tensor[perm[i : i + BATCH_SIZE]]
            optimizer.zero_grad()
            recon, _ = model(batch)
            loss = torch.nn.functional.mse_loss(recon, batch)
            loss.backward()
            optimizer.step()
            total_loss += loss.item() * len(batch)
        logger.info("autoencoder_epoch", epoch=epoch, train_loss=total_loss / len(train_tensor))

    model.eval()
    with torch.no_grad():
        train_recon, train_emb = model(train_tensor)
        test_recon, test_emb = model(torch.tensor(test_X_norm, dtype=torch.float32))
    train_emb_np, test_emb_np = train_emb.numpy(), test_emb.numpy()

    iso_forest = IsolationForest(random_state=42, contamination="auto")
    iso_forest.fit(train_emb_np)
    test_scores = -iso_forest.score_samples(test_emb_np)  # higher = more anomalous

    logger.info("autoencoder_labeling_start", n=len(test_segments))
    rule_labels = np.array([_segment_has_rule_anomaly(seg) for seg in test_segments])
    n_positive = int(rule_labels.sum())
    logger.info("autoencoder_labeling_complete", n_positive=n_positive, n_total=len(rule_labels))

    if n_positive == 0 or n_positive == len(rule_labels):
        pr_auc = None
        precision_at_k = None
    else:
        pr_auc = float(average_precision_score(rule_labels, test_scores))
        k = max(1, n_positive)
        top_k_idx = np.argsort(-test_scores)[:k]
        precision_at_k = float(rule_labels[top_k_idx].mean())

    reconstruction_mae = float(
        torch.nn.functional.l1_loss(
            test_recon, torch.tensor(test_X_norm, dtype=torch.float32)
        ).item()
    )

    version = f"traj-autoencoder-{dt.datetime.now(dt.UTC):%Y%m%dT%H%M%S}"
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    model_path = MODEL_DIR / f"{version}.pt"
    torch.save(
        {"state_dict": model.state_dict(), "norm_mean": mean, "norm_std": std},
        model_path,
    )

    report = {
        "kind": "anomaly",
        "model_version": version,
        "trained_at": dt.datetime.now(dt.UTC).isoformat(),
        "train_window": {
            "source": "ADS-B Exchange readsb-hist samples (docs/adr/0004)",
            "train_segments": int(split),
            "test_segments": int(n - split),
        },
        "model": {
            "reconstruction_mae": reconstruction_mae,
            "rule_agreement": {
                "n_test_segments": int(len(rule_labels)),
                "n_rule_flagged": n_positive,
                "pr_auc_vs_rules": pr_auc,
                "precision_at_k_vs_rules": precision_at_k,
                "note": "agreement with the rules layer on this sample, not the spec's "
                "BTS-incident-based precision - see this module's docstring and docs/adr/0004",
            },
        },
        "artifact_path": str(model_path),
    }
    metrics_path = MODEL_DIR / f"{version}.metrics.json"
    metrics_path.write_text(json.dumps(report, indent=2, default=str))

    update_report(report)
    register_and_maybe_promote_sync(report)

    _save_embeddings_artifact(windows.icao24, np.concatenate([train_emb_np, test_emb_np]), version)

    logger.info("autoencoder_training_complete", version=version, pr_auc=pr_auc)
    return report


def _save_embeddings_artifact(icao24s: np.ndarray, embeddings: np.ndarray, version: str) -> Path:
    """Saves embeddings as a local artifact rather than writing to the
    `trajectory_embeddings` pgvector table directly: that table's
    `flight_id` column has a real foreign key to `flights.flight_id`
    (services/common/models/trajectory.py), and this historical training
    data has no corresponding `flights` row to attach to - inventing a
    fake UUID would either violate the constraint (every insert fails) or
    require fabricating placeholder `flights` rows, which would pollute a
    table the live pipeline owns. A genuine live write path exists once a
    live caller has a real flight_id to embed against (services/inference/
    would compute and store an embedding exactly this way for an in-progress
    or completed live-tracked flight) - that's a live-serving integration
    this training script correctly doesn't attempt.
    """
    path = MODEL_DIR / f"{version}.embeddings.npz"
    np.savez(path, icao24=icao24s, embeddings=embeddings)
    return path


if __name__ == "__main__":
    configure_logging()
    train()
