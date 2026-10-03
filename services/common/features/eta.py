"""The SINGLE feature-computation implementation for the ETA model (M2),
imported identically by `ml/train/train_eta.py` and (once it exists)
`services/inference/eta.py` - same role as `features/trajectory.py` for M1,
see that module's docstring and execution rule 5 ("never duplicate feature
logic").

v1 scope note: the master spec's feature list for M2 includes "distance-to-
go" and "headwind component" computed from an aircraft's live in-flight
position - that needs historical ADS-B track data joined against BTS ground
truth, which doesn't exist yet (no backfill job has accumulated it). This
version trains on what's known at or shortly after departure: scheduled
block distance/time, the departure delay already observed, and the
destination's scheduled congestion - a legitimate, narrower framing of "ETA
regression" (predict total arrival delay from early-flight signals) rather
than the full mid-flight live version. Extending to true mid-flight
features is a drop-in change here once that historical join exists; nothing
downstream needs to change shape-wise beyond adding fields.

Deliberately has no dependency on lightgbm/pandas: pure Python over
primitive inputs, so it is identically callable from a pandas apply() during
training and from a live request handler during serving.
"""

from __future__ import annotations

from dataclasses import dataclass

# Fallback floors when a (destination, carrier) pair has no historical
# taxi-in observations in the training fold at all (e.g. a brand-new route) -
# a global median computed by the caller from the same training fold, never
# a hardcoded guess.
DEFAULT_TAXI_IN_MIN = 10.0


@dataclass(frozen=True)
class ETAFeatures:
    """One feature vector for one flight, ready to hand to the model
    (training) or the LightGBM runtime (serving) - field order IS the
    model's input order, same contract as TrajectoryFeatures.
    """

    distance_mi: float
    sched_elapsed_min: float
    sched_block_speed_kt: float
    dep_delay_min: float
    hour_of_day: int
    day_of_week: int
    month: int
    dest_sched_arrivals_30min: int
    historic_taxi_in_p50_min: float
    carrier: str
    origin: str
    dest: str


def hour_of_day_from_hhmm(hhmm: str | int) -> int:
    """BTS (and our own CRS-time fields) encode time-of-day as "HHMM", e.g.
    "905" for 9:05am or "2350" for 11:50pm - zero-padding is inconsistent
    (905 vs 0905) depending on source, so this normalizes via int() rather
    than string slicing.
    """
    value = int(hhmm)
    return (value // 100) % 24


def compute_eta_features(
    *,
    distance_mi: float,
    sched_elapsed_min: float,
    dep_delay_min: float,
    sched_dep_hhmm: str | int,
    day_of_week: int,
    month: int,
    carrier: str,
    origin: str,
    dest: str,
    dest_sched_arrivals_30min: int,
    historic_taxi_in_p50_min: float | None,
) -> ETAFeatures:
    """Pure feature computation - no DB/file I/O. Resolving
    `dest_sched_arrivals_30min` (destination congestion) and
    `historic_taxi_in_p50_min` (per (dest, carrier) historical median) is the
    caller's job: `ml/datasets/eta.py` computes them from the training fold
    of a BTS month; a live-serving caller would resolve them from the
    `flights` table and a precomputed lookup, respectively.
    """
    if sched_elapsed_min <= 0:
        raise ValueError(f"sched_elapsed_min must be positive, got {sched_elapsed_min}")

    block_speed_kt = distance_mi / (sched_elapsed_min / 60) * 0.868976  # mph -> kt

    return ETAFeatures(
        distance_mi=distance_mi,
        sched_elapsed_min=sched_elapsed_min,
        sched_block_speed_kt=block_speed_kt,
        dep_delay_min=dep_delay_min,
        hour_of_day=hour_of_day_from_hhmm(sched_dep_hhmm),
        day_of_week=day_of_week,
        month=month,
        dest_sched_arrivals_30min=dest_sched_arrivals_30min,
        historic_taxi_in_p50_min=(
            historic_taxi_in_p50_min
            if historic_taxi_in_p50_min is not None
            else DEFAULT_TAXI_IN_MIN
        ),
        carrier=carrier,
        origin=origin,
        dest=dest,
    )


# Column order the model is trained/served on - every consumer that builds a
# row for the model (training's DataFrame, a live inference request) uses
# this instead of re-deriving field order from ETAFeatures by hand.
FEATURE_COLUMNS = [
    "distance_mi",
    "sched_elapsed_min",
    "sched_block_speed_kt",
    "dep_delay_min",
    "hour_of_day",
    "day_of_week",
    "month",
    "dest_sched_arrivals_30min",
    "historic_taxi_in_p50_min",
    "carrier",
    "origin",
    "dest",
]

CATEGORICAL_COLUMNS = ["carrier", "origin", "dest"]
