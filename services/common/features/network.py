"""The SINGLE feature-computation implementation for the delay-propagation
model (M3), imported identically by ml/datasets/network.py (training) and a
live-serving caller (the `/airports/{icao}/delay-forecast` endpoint) - same
role and same reasoning as features/eta.py and features/trajectory.py, see
their docstrings and execution rule 5 ("never duplicate feature logic").

v1 scope note (docs/adr/0004): the master spec's node-feature list includes
ceiling/visibility/wind, computed from live weather observations - this
project has no weather-ingestion pipeline at all (`weather_obs` exists as a
schema with zero rows). This v1 computes the features that ARE available
from scheduled-flight data alone: delay/ops/cancellation statistics and
calendar context. The model still exercises its real differentiating
mechanism (the rotation/flow graph structure propagating delay between
airports), just without the weather covariate - a known, stated limitation,
not a hidden one.

Deliberately has no dependency on torch/pandas: pure Python over primitive
aggregate inputs, identically callable from a pandas groupby/apply during
training and from a live request handler during serving.
"""

from __future__ import annotations

from dataclasses import dataclass

# A short, fixed list of US federal holidays likely to show distinct demand/
# delay patterns in CONUS air travel - intentionally not an exhaustive
# calendar library dependency for a feature this coarse (a single boolean).
US_HOLIDAYS_2024 = frozenset(
    [
        "2024-01-01",  # New Year's Day
        "2024-01-15",  # MLK Day
        "2024-02-19",  # Presidents' Day
        "2024-05-27",  # Memorial Day
        "2024-06-19",  # Juneteenth
        "2024-07-04",  # Independence Day
        "2024-09-02",  # Labor Day
        "2024-11-28",  # Thanksgiving
        "2024-11-29",  # Day after Thanksgiving (observed travel-heavy)
        "2024-12-24",
        "2024-12-25",
        "2024-12-31",
    ]
)


@dataclass(frozen=True)
class NetworkFeatures:
    """One node's feature vector for one 15-minute bucket - field order IS
    the model's per-node input order, same contract as ETAFeatures/
    TrajectoryFeatures.
    """

    mean_dep_delay_min: float
    mean_arr_delay_min: float
    ops_count: int
    cancellations: int
    hour_of_day: int
    day_of_week: int
    is_holiday: int  # 0/1, kept as int (not bool) for direct tensor construction


def compute_network_features(
    *,
    mean_dep_delay_min: float | None,
    mean_arr_delay_min: float | None,
    ops_count: int,
    cancellations: int,
    bucket_hour: int,
    bucket_day_of_week: int,
    bucket_date_iso: str,
) -> NetworkFeatures:
    """Pure feature computation - no DB/file I/O. Resolving the raw
    aggregate inputs (mean delay, ops count, etc. for one airport in one
    15-minute bucket) is the caller's job: ml/datasets/network.py computes
    them from a BTS training window; a live-serving caller would resolve
    them from the `flights` table for the current/recent bucket.

    A bucket with zero operations has no real mean delay to report - 0.0 is
    used as a neutral filler (not a claim that delay was literally zero),
    consistent with how mean_dep_delay_min/mean_arr_delay_min are only ever
    meaningful alongside ops_count > 0.
    """
    return NetworkFeatures(
        mean_dep_delay_min=mean_dep_delay_min if mean_dep_delay_min is not None else 0.0,
        mean_arr_delay_min=mean_arr_delay_min if mean_arr_delay_min is not None else 0.0,
        ops_count=ops_count,
        cancellations=cancellations,
        hour_of_day=bucket_hour,
        day_of_week=bucket_day_of_week,
        is_holiday=1 if bucket_date_iso in US_HOLIDAYS_2024 else 0,
    )


# Column order the model is trained/served on.
FEATURE_COLUMNS = [
    "mean_dep_delay_min",
    "mean_arr_delay_min",
    "ops_count",
    "cancellations",
    "hour_of_day",
    "day_of_week",
    "is_holiday",
]
