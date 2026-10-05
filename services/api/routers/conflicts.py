"""GET /api/v1/conflicts - M5 (master spec §6/§7): candidate conflict pairs
within a bbox, pruned by H3 k-ring + altitude band, scored by Monte Carlo
sampling from M1's quantile predictions (services/inference/conflict.py).

Honest caveat: this endpoint calls predict_trajectory once per in-bbox
aircraft (services/inference/trajectory.py), each of which needs
WINDOW_SIZE recent state_vectors rows and a promoted "trajectory" model -
on a system with little accumulated live history or no promoted M1 model
yet, most or all aircraft will have no prediction and contribute no
candidate pairs, returned as an empty list rather than a fabricated one.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.config import get_settings
from services.common.db import get_session
from services.common.determinism import Rng
from services.inference.conflict import (
    AircraftSnapshot,
    QuantilePrediction,
    monte_carlo_conflict_probability,
    prune_candidate_pairs,
)
from services.inference.trajectory import predict_trajectory

router = APIRouter(prefix="/conflicts", tags=["conflicts"])

# A fixed seed, not wall-clock-derived - master spec §8's determinism
# requirement ("a single seeded RNG... no wall-clock reads") applies to M5
# too, even outside the simulator proper: the same aircraft snapshot and
# predictions must always score the same conflict probability.
_CONFLICT_SEED = 20240101


@router.get("")
async def list_conflicts(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    min_prob: float = Query(0.1, ge=0.0, le=1.0),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    if min_lat > max_lat or min_lon > max_lon:
        raise HTTPException(400, "bbox min must be <= max")

    rows = (
        await session.execute(
            text(
                """
                SELECT DISTINCT ON (icao24) icao24, lat, lon, baro_alt_ft
                FROM state_vectors
                WHERE ts > now() - make_interval(secs => :staleness)
                  AND lat BETWEEN :min_lat AND :max_lat
                  AND lon BETWEEN :min_lon AND :max_lon
                  AND baro_alt_ft IS NOT NULL
                ORDER BY icao24, ts DESC
                """
            ),
            {
                "staleness": get_settings().live_staleness_seconds,
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lon": min_lon,
                "max_lon": max_lon,
            },
        )
    ).all()

    snapshots = [
        AircraftSnapshot(icao24=r.icao24, lat=r.lat, lon=r.lon, alt_ft=r.baro_alt_ft) for r in rows
    ]
    candidate_pairs = prune_candidate_pairs(snapshots)
    if not candidate_pairs:
        return []

    # One predict_trajectory call per aircraft that appears in at least one
    # candidate pair - not every aircraft in the bbox, since most won't be
    # near enough to anything else to matter (prune_candidate_pairs already
    # filtered that out cheaply, before paying for any model inference).
    needed_indices = {i for pair in candidate_pairs for i in pair}
    predictions: dict[int, list[QuantilePrediction]] = {}
    for i in needed_indices:
        result = await predict_trajectory(session, snapshots[i].icao24)
        if result is None:
            continue
        predictions[i] = [
            QuantilePrediction(
                horizon_s=h["horizon_s"],
                q10=tuple(h["q10"]),
                q50=tuple(h["q50"]),
                q90=tuple(h["q90"]),
            )
            for h in result["horizons"]
        ]

    conflicts = []
    rng = Rng(_CONFLICT_SEED)
    for i, j in candidate_pairs:
        if i not in predictions or j not in predictions:
            continue
        prob = monte_carlo_conflict_probability(
            snapshots[i], snapshots[j], predictions[i], predictions[j], rng.spawn(f"{i}:{j}")
        )
        if prob >= min_prob:
            conflicts.append(
                {
                    "icao24_a": snapshots[i].icao24,
                    "icao24_b": snapshots[j].icao24,
                    "probability": prob,
                }
            )

    return conflicts
