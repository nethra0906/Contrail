"""Evaluation metrics - the ONLY place a reported number gets computed, per
execution rule 3: "Never write a metric by hand. Metrics come from ml/eval/
only." Every figure in docs/ml-report.md traces back to a call in this
module, via ml/eval/report.py.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# M2's spec'd buckets are by time-to-arrival; this v1 trains on pre-departure
# features (see services/common/features/eta.py), so scheduled flight
# duration stands in for time-to-arrival - see that module's docstring for
# why, and for what closes the gap later.
DURATION_BUCKETS: list[tuple[str, float, float]] = [
    ("<15min", 0, 15),
    ("15-45min", 15, 45),
    ("45-120min", 45, 120),
    (">120min", 120, float("inf")),
]


def mae(y_true: pd.Series, y_pred: pd.Series) -> float:
    return float(np.mean(np.abs(np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float))))


def p90_abs_error(y_true: pd.Series, y_pred: pd.Series) -> float:
    errors = np.abs(np.asarray(y_true, dtype=float) - np.asarray(y_pred, dtype=float))
    return float(np.percentile(errors, 90))


def eta_metrics(y_true: pd.Series, y_pred: pd.Series, duration_min: pd.Series) -> dict:
    """MAE / P90 absolute error (minutes) overall and bucketed by scheduled
    flight duration. `y_true`, `y_pred`, `duration_min` must share an index.
    """
    overall = {
        "mae_min": mae(y_true, y_pred),
        "p90_min": p90_abs_error(y_true, y_pred),
        "n": int(len(y_true)),
    }

    by_bucket: dict[str, dict] = {}
    for label, lo, hi in DURATION_BUCKETS:
        mask = (duration_min >= lo) & (duration_min < hi)
        n = int(mask.sum())
        if n == 0:
            continue
        by_bucket[label] = {
            "mae_min": mae(y_true[mask], y_pred[mask]),
            "p90_min": p90_abs_error(y_true[mask], y_pred[mask]),
            "n": n,
        }

    return {"overall": overall, "by_duration_bucket": by_bucket}
