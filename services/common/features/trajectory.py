"""The SINGLE feature-computation implementation for the trajectory model
(M1), imported identically by ml/train/train_trajectory.py and
services/inference/trajectory.py. See docs/CONTRAIL_MASTER_SPEC.md §7 and
execution rule 5 ("never duplicate feature logic") - this module existing
and being the only place this math happens is what makes the Stage 4
train/serve parity test meaningful rather than a tautology.

Deliberately has NO dependency on torch/numpy/pandas: it's pure Python over
the same geo primitives ingestion and the simulator already use, so it can
be imported (and tested) by any service without pulling in the ML extras.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.common.geo import LatLon, initial_bearing_deg, to_enu

# How many trailing observations M1 conditions on - see spec §7, "12
# resampled states over 60 s". Exposed as a constant so training and serving
# windowing code both anchor on the same number rather than each guessing.
WINDOW_SIZE = 12


@dataclass(frozen=True)
class TrackPoint:
    """One resampled observation in a trajectory window. `ts_offset_s` is
    seconds since the window's first point - relative time, not wall clock,
    since the model only ever sees a short local window.
    """

    ts_offset_s: float
    lat: float
    lon: float
    alt_ft: float | None
    vert_rate_fpm: float | None
    heading_deg: float | None


@dataclass(frozen=True)
class TrajectoryFeatures:
    """One feature vector for one window, ready to hand to the model
    (training) or the ONNX runtime (serving) - this dataclass's field order
    IS the model's input order; changing it is a breaking model-compat
    change, not a free refactor.
    """

    # Local ENU-frame position deltas (metres) relative to the window's first
    # point, one pair per point after the first (WINDOW_SIZE - 1 pairs).
    east_deltas_m: list[float]
    north_deltas_m: list[float]
    alt_deltas_ft: list[float]
    # Per-step kinematics, one value per consecutive pair (WINDOW_SIZE - 1).
    groundspeed_kt: list[float]
    vertical_rate_fpm: list[float]
    turn_rate_deg_s: list[float]
    # Static/contextual features, one value for the whole window.
    bearing_to_destination_deg: float | None
    distance_to_destination_km: float | None
    phase_onehot: dict[str, int]


def _bearing_delta(a: float, b: float) -> float:
    """Signed shortest angular difference b - a, in [-180, 180)."""
    return (b - a + 180) % 360 - 180


def compute_trajectory_features(
    window: list[TrackPoint],
    destination: LatLon | None = None,
    phase: str = "unknown",
    phases: tuple[str, ...] = ("ground", "climb", "cruise", "descent", "approach", "unknown"),
) -> TrajectoryFeatures:
    """Compute M1's input features for one window.

    Raises ValueError if the window is shorter than 2 points - you cannot
    compute any kinematic feature (speed, vertical rate, turn rate) from a
    single point, and the caller (both training's windowing code and the
    live inference buffer) is expected to simply not call this until it has
    enough history, rather than this function silently padding with zeros
    that would look like "the aircraft isn't moving."
    """
    if len(window) < 2:
        raise ValueError(f"need at least 2 points to compute kinematic features, got {len(window)}")

    anchor = LatLon(window[0].lat, window[0].lon)

    east_deltas: list[float] = []
    north_deltas: list[float] = []
    alt_deltas: list[float] = []
    groundspeed: list[float] = []
    vertical_rate: list[float] = []
    turn_rate: list[float] = []

    for i in range(1, len(window)):
        prev, cur = window[i - 1], window[i]
        dt_s = cur.ts_offset_s - prev.ts_offset_s
        if dt_s <= 0:
            raise ValueError(f"window points must be strictly increasing in time, got dt={dt_s}")

        east, north = to_enu(LatLon(cur.lat, cur.lon), anchor)
        east_deltas.append(east)
        north_deltas.append(north)

        prev_east, prev_north = to_enu(LatLon(prev.lat, prev.lon), anchor)
        step_dist_m = ((east - prev_east) ** 2 + (north - prev_north) ** 2) ** 0.5
        groundspeed.append(step_dist_m / dt_s * 1.94384)  # m/s -> knots

        if cur.alt_ft is not None and prev.alt_ft is not None:
            alt_deltas.append(cur.alt_ft - prev.alt_ft)
            vertical_rate.append((cur.alt_ft - prev.alt_ft) / dt_s * 60)  # ft/s -> ft/min
        else:
            alt_deltas.append(0.0)
            vertical_rate.append(0.0)

        if cur.heading_deg is not None and prev.heading_deg is not None:
            turn_rate.append(_bearing_delta(prev.heading_deg, cur.heading_deg) / dt_s)
        else:
            turn_rate.append(0.0)

    bearing_to_dest = None
    distance_to_dest_km = None
    if destination is not None:
        last = LatLon(window[-1].lat, window[-1].lon)
        bearing_to_dest = initial_bearing_deg(last, destination)
        from services.common.geo import haversine_distance_m

        distance_to_dest_km = haversine_distance_m(last, destination) / 1000

    phase_onehot = {p: (1 if p == phase else 0) for p in phases}

    return TrajectoryFeatures(
        east_deltas_m=east_deltas,
        north_deltas_m=north_deltas,
        alt_deltas_ft=alt_deltas,
        groundspeed_kt=groundspeed,
        vertical_rate_fpm=vertical_rate,
        turn_rate_deg_s=turn_rate,
        bearing_to_destination_deg=bearing_to_dest,
        distance_to_destination_km=distance_to_dest_km,
        phase_onehot=phase_onehot,
    )
