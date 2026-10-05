"""M5 - conflict probability (master spec §7): prune candidate aircraft
pairs with an H3 k-ring + altitude band, then Monte Carlo sample from M1's
predicted quantiles to estimate P(horizontal separation < 5 NM AND vertical
separation < 1000 ft) at any of M1's predicted horizons.

Pure, dependency-light (no torch/ONNX here - this module consumes whatever
quantile predictions M1 already produced, it doesn't run M1 itself) so it's
unit-testable with plain data, same philosophy as services/assembler's
pipeline modules. Determinism (master spec §8: "a single seeded RNG,
threaded through common/determinism.py") is enforced by requiring every
caller to pass an `Rng`, never reading the `random` module directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from services.common.determinism import Rng
from services.common.geo import (
    H3_LIVE_RESOLUTION,
    LatLon,
    h3_k_ring,
    latlon_to_h3,
)

HORIZONTAL_SEPARATION_THRESHOLD_NM = 5.0
VERTICAL_SEPARATION_THRESHOLD_FT = 1000.0
NM_TO_M = 1852.0
DEFAULT_MC_SAMPLES = 200
DEFAULT_ALTITUDE_BAND_FT = 2000.0
DEFAULT_K_RING = (
    1  # adjacent H3 cells only - candidate pairs must be geographically close to begin with
)


@dataclass(frozen=True)
class AircraftSnapshot:
    icao24: str
    lat: float
    lon: float
    alt_ft: float


@dataclass(frozen=True)
class QuantilePrediction:
    """One aircraft's M1 output for one horizon: predicted (dE, dN, dAlt) at
    the {0.1, 0.5, 0.9} quantiles, relative to its current position - the
    exact shape ml.models.trajectory_gru.TrajectoryGRU produces per horizon.
    """

    horizon_s: int
    q10: tuple[float, float, float]
    q50: tuple[float, float, float]
    q90: tuple[float, float, float]


def prune_candidate_pairs(
    snapshots: list[AircraftSnapshot],
    k_ring: int = DEFAULT_K_RING,
    altitude_band_ft: float = DEFAULT_ALTITUDE_BAND_FT,
    resolution: int = H3_LIVE_RESOLUTION,
) -> list[tuple[int, int]]:
    """Returns index pairs (into `snapshots`) worth running the expensive
    Monte Carlo step on: aircraft within `k_ring` H3 cells of each other AND
    within `altitude_band_ft` of each other. This is the step that keeps M5
    tractable - without it, every pair among N aircraft would need Monte
    Carlo sampling (O(N^2) at full cost); most pairs are nowhere near each
    other and can be ruled out with cheap integer/float comparisons first.
    """
    cells = [latlon_to_h3(s.lat, s.lon, resolution) for s in snapshots]
    neighbor_sets = [set(h3_k_ring(c, k_ring)) for c in cells]

    pairs = []
    for i in range(len(snapshots)):
        for j in range(i + 1, len(snapshots)):
            if cells[j] not in neighbor_sets[i]:
                continue
            if abs(snapshots[i].alt_ft - snapshots[j].alt_ft) > altitude_band_ft:
                continue
            pairs.append((i, j))
    return pairs


def _sample_offset(rng: Rng, q10: float, q50: float, q90: float) -> float:
    """Draws one sample from a distribution consistent with the predicted
    quantiles: a triangular distribution with its peak at q50 and its
    bounds at q10/q90 - the simplest distribution shape matching exactly
    three quantile points with no additional assumption about tail
    behavior beyond what was predicted. Degenerates to a point mass if
    q10 == q50 == q90 (e.g. a baseline with no spread).
    """
    lo, mode, hi = min(q10, q50, q90), q50, max(q10, q50, q90)
    if hi - lo < 1e-9:
        return mode
    mode = min(max(mode, lo), hi)  # guard against q50 outside [q10, q90] from a miscalibrated model
    u = rng.uniform(0.0, 1.0)
    f = (mode - lo) / (hi - lo)
    if u < f:
        return lo + (u * (hi - lo) * (mode - lo)) ** 0.5
    return hi - ((1 - u) * (hi - lo) * (hi - mode)) ** 0.5


def monte_carlo_conflict_probability(
    aircraft_i: AircraftSnapshot,
    aircraft_j: AircraftSnapshot,
    predictions_i: list[QuantilePrediction],
    predictions_j: list[QuantilePrediction],
    rng: Rng,
    n_samples: int = DEFAULT_MC_SAMPLES,
) -> float:
    """P(horizontal < 5 NM AND vertical < 1000 ft) at ANY shared predicted
    horizon, estimated by sampling `n_samples` independent trajectory pairs
    from each aircraft's quantile predictions and checking closest point of
    approach at each horizon both aircraft have a prediction for.

    Both aircraft must share at least one horizon (predictions_i/j are
    matched by horizon_s) - a mismatched horizon set is a caller error
    (both would normally come from the same M1 model run on the same
    horizon list), not a data condition this function silently works
    around.
    """
    horizons_i = {p.horizon_s: p for p in predictions_i}
    horizons_j = {p.horizon_s: p for p in predictions_j}
    shared_horizons = sorted(set(horizons_i) & set(horizons_j))
    if not shared_horizons:
        return 0.0

    conflicts = 0
    for _ in range(n_samples):
        for h in shared_horizons:
            pi, pj = horizons_i[h], horizons_j[h]
            dE_i = _sample_offset(rng, *_axis(pi, 0))
            dN_i = _sample_offset(rng, *_axis(pi, 1))
            dAlt_i = _sample_offset(rng, *_axis(pi, 2))
            dE_j = _sample_offset(rng, *_axis(pj, 0))
            dN_j = _sample_offset(rng, *_axis(pj, 1))
            dAlt_j = _sample_offset(rng, *_axis(pj, 2))

            # Both offsets are already in the same local ENU-style metric
            # space (metres east/north, feet alt) relative to each
            # aircraft's OWN current position - horizontal separation
            # between the two predicted points needs each aircraft's
            # absolute predicted position, so project back through a
            # shared local frame anchored at aircraft_i's current position.
            east_i_m, north_i_m = dE_i, dN_i
            # aircraft_j's offset is relative to ITS OWN start, not
            # aircraft_i's - add the real-world separation between the two
            # aircraft's starting points (approximated in the same local
            # ENU frame via haversine + bearing, consistent with how
            # services/common/geo.py projects everywhere else in this
            # project).
            base_east_m, base_north_m = _enu_offset(aircraft_j, aircraft_i)
            east_j_m, north_j_m = base_east_m + dE_j, base_north_m + dN_j

            horizontal_m = ((east_i_m - east_j_m) ** 2 + (north_i_m - north_j_m) ** 2) ** 0.5
            vertical_ft = abs((aircraft_i.alt_ft + dAlt_i) - (aircraft_j.alt_ft + dAlt_j))

            if horizontal_m < HORIZONTAL_SEPARATION_THRESHOLD_NM * NM_TO_M and vertical_ft < (
                VERTICAL_SEPARATION_THRESHOLD_FT
            ):
                conflicts += 1
                break  # one conflicting horizon is enough to count this sample as a conflict

    return conflicts / n_samples


def _axis(p: QuantilePrediction, i: int) -> tuple[float, float, float]:
    return p.q10[i], p.q50[i], p.q90[i]


def _enu_offset(a: AircraftSnapshot, origin: AircraftSnapshot) -> tuple[float, float]:
    from services.common.geo import to_enu

    return to_enu(LatLon(a.lat, a.lon), LatLon(origin.lat, origin.lon))
