"""M1 evaluation metrics (master spec §7): median + P90 horizontal error per
horizon, CRPS, PIT-style calibration histogram, and 80% interval coverage.
The single source of truth every M1 report number comes from - mirrors
ml/eval/metrics.py's role for M2 (never computed ad hoc elsewhere).
"""

from __future__ import annotations

import numpy as np

from ml.datasets.trajectory import HORIZONS_S


def horizontal_error_m(
    pred_east_m: np.ndarray, pred_north_m: np.ndarray, y: np.ndarray
) -> np.ndarray:
    """Euclidean (great-circle-adjacent, since these are local ENU deltas
    over short distances) horizontal error in meters, for one point
    prediction against the true (dE, dN, dAlt) target.
    """
    return np.sqrt((pred_east_m - y[..., 0]) ** 2 + (pred_north_m - y[..., 1]) ** 2)


def per_horizon_error_summary(point_pred: np.ndarray, y: np.ndarray, y_mask: np.ndarray) -> dict:
    """point_pred/y: (N, H, 3). y_mask: (N, H) bool - only masked-in targets
    contribute, consistent with ml/datasets/trajectory.py never fabricating
    a target for a horizon with no real ground truth.

    Returns {horizon_s: {median_error_km, p90_error_km, n}} - median/P90
    exactly as the spec asks, reported per horizon, never collapsed into one
    number (error at 60s and error at 900s are not comparable and
    shouldn't be averaged together).
    """
    err_m = horizontal_error_m(point_pred[..., 0], point_pred[..., 1], y)  # (N, H)
    out: dict[int, dict] = {}
    for h_i, horizon in enumerate(HORIZONS_S):
        mask = y_mask[:, h_i]
        if not mask.any():
            out[horizon] = {"median_error_km": None, "p90_error_km": None, "n": 0}
            continue
        vals_km = err_m[mask, h_i] / 1000.0
        out[horizon] = {
            "median_error_km": float(np.median(vals_km)),
            "p90_error_km": float(np.percentile(vals_km, 90)),
            "n": int(mask.sum()),
        }
    return out


def interval_coverage_80(
    q10: np.ndarray, q90: np.ndarray, y: np.ndarray, y_mask: np.ndarray
) -> dict:
    """Fraction of true horizontal positions falling inside the [q10, q90]
    predicted box per axis (east, north) - a well-calibrated 80% interval
    should contain the truth roughly 80% of the time, no more, no less.
    Reported per horizon.
    """
    out: dict[int, float | None] = {}
    for h_i, horizon in enumerate(HORIZONS_S):
        mask = y_mask[:, h_i]
        if not mask.any():
            out[horizon] = None
            continue
        inside_east = (y[mask, h_i, 0] >= q10[mask, h_i, 0]) & (
            y[mask, h_i, 0] <= q90[mask, h_i, 0]
        )
        inside_north = (y[mask, h_i, 1] >= q10[mask, h_i, 1]) & (
            y[mask, h_i, 1] <= q90[mask, h_i, 1]
        )
        out[horizon] = float((inside_east & inside_north).mean())
    return out


def pit_histogram(
    q10: np.ndarray,
    q50: np.ndarray,
    q90: np.ndarray,
    y: np.ndarray,
    y_mask: np.ndarray,
    axis: int = 0,
) -> dict:
    """A 3-quantile approximation of a PIT (probability integral transform)
    histogram: with only {0.1, 0.5, 0.9} predicted, the finest calibration
    check available is which of the four regions the truth falls into
    (below q10 / q10-q50 / q50-q90 / above q90). A perfectly calibrated
    model would show roughly (10%, 40%, 40%, 10%) - skewed bins indicate
    the quantile heads are mis-calibrated (e.g. consistently too narrow or
    biased to one side), the thing this check exists to catch. `axis`
    selects which target dimension (0=east, 1=north, 2=alt) to check.
    """
    out: dict[int, dict | None] = {}
    for h_i, horizon in enumerate(HORIZONS_S):
        mask = y_mask[:, h_i]
        if not mask.any():
            out[horizon] = None
            continue
        truth = y[mask, h_i, axis]
        lo, mid, hi = q10[mask, h_i, axis], q50[mask, h_i, axis], q90[mask, h_i, axis]
        n = len(truth)
        below_q10 = (truth < lo).sum()
        q10_to_q50 = ((truth >= lo) & (truth < mid)).sum()
        q50_to_q90 = ((truth >= mid) & (truth < hi)).sum()
        above_q90 = (truth >= hi).sum()
        out[horizon] = {
            "below_q10": below_q10 / n,
            "q10_to_q50": q10_to_q50 / n,
            "q50_to_q90": q50_to_q90 / n,
            "above_q90": above_q90 / n,
        }
    return out


def pinball_loss(pred: np.ndarray, target: np.ndarray, quantile: float) -> np.ndarray:
    """The asymmetric loss quantile regression trains on - also usable
    standalone as a calibration-sensitive error metric (reported as CRPS's
    3-quantile approximation below: the average pinball loss across
    {0.1, 0.5, 0.9} is a coarse but real approximation of CRPS, the
    continuous ranked probability score, exact only in the limit of
    infinitely many quantiles).
    """
    diff = target - pred
    return np.maximum(quantile * diff, (quantile - 1) * diff)


def approximate_crps(
    q10: np.ndarray, q50: np.ndarray, q90: np.ndarray, y: np.ndarray, y_mask: np.ndarray
) -> dict:
    """Average pinball loss across the three predicted quantiles and both
    horizontal axes, per horizon - a coarse (3-point) approximation of CRPS,
    labeled as such rather than claiming the exact score a full predictive
    distribution would give.
    """
    out: dict[int, float | None] = {}
    for h_i, horizon in enumerate(HORIZONS_S):
        mask = y_mask[:, h_i]
        if not mask.any():
            out[horizon] = None
            continue
        losses = []
        for axis in (
            0,
            1,
        ):  # east, north - altitude's quantile loss is reported separately if needed
            truth = y[mask, h_i, axis]
            for q, pred in ((0.1, q10), (0.5, q50), (0.9, q90)):
                losses.append(pinball_loss(pred[mask, h_i, axis], truth, q))
        out[horizon] = float(np.mean(np.concatenate(losses)))
    return out
