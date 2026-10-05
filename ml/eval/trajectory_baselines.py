"""M1 baseline (master spec §7: "MUST implement first" before the GRU):
constant-velocity dead reckoning. Projects the aircraft's last observed
velocity vector forward at each horizon - no model, just physics, and
exactly the bar the GRU has to clear to be worth the extra complexity.
"""

from __future__ import annotations

import numpy as np

from ml.datasets.trajectory import HORIZONS_S

NATIVE_STEP_S = 5.0  # the ADS-B Exchange sample data's native cadence (docs/adr/0004)


def constant_velocity_predict(X: np.ndarray) -> np.ndarray:
    """X: (N, WINDOW_SIZE-1, NUM_STEP_FEATURES) - columns
    [east_delta_m, north_delta_m, alt_delta_ft, gs_kt, vrate_fpm, turn_rate],
    where east/north/alt_delta are CUMULATIVE offsets from the window's
    first point (see services/common/features/trajectory.py's
    compute_trajectory_features - that's the field order the model is
    trained on, and this baseline must use the same raw input columns to
    be a fair comparison).

    The last 5s step's actual (not cumulative) displacement is the
    difference between the last two cumulative offsets - dividing the
    LAST COLUMN directly by NATIVE_STEP_S would instead divide the
    displacement across the entire ~55s window by 5s, overstating speed by
    roughly 11x (a real bug caught by comparing this baseline's output
    against physically plausible aircraft speeds, fixed here). Projects
    that corrected instantaneous velocity linearly forward to each horizon
    - the simplest physically-grounded prediction, with no notion of
    turning or decelerating.

    Returns point predictions only, shape (N, len(HORIZONS_S), 3) - a
    dead-reckoning baseline has no calibrated uncertainty to report, so
    there's nothing to compare against the GRU's quantile spread except its
    median (q50) head.
    """
    last_step_delta = X[:, -1, :3] - X[:, -2, :3]  # (N, 3): [east, north, alt], last 5s only
    velocity_per_s = last_step_delta / NATIVE_STEP_S

    horizons = np.asarray(HORIZONS_S, dtype=np.float32)  # (H,)
    projected = velocity_per_s[:, None, :] * horizons[None, :, None]  # (N, H, 3)

    return projected
