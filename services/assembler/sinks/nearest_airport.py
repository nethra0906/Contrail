"""Nearest-airport lookup for leg detection.

Only called on a takeoff/landing edge (a handful of times per aircraft per
day), never per state-vector tick, so a bounding-box pre-filter in SQL
followed by exact haversine ranking in Python is plenty fast without needing
pgvector or PostGIS for this.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.geo import LatLon, haversine_distance_m

# An aircraft's takeoff/landing point can be a kilometre or two from the
# airport reference point (runway threshold vs. field centre) but never much
# more - beyond this it's not a match, and leg_detect should treat it as
# unresolved rather than attributing the leg to the wrong airport.
MAX_MATCH_DISTANCE_M = 15_000

# Degrees of lat/lon padding for the bounding-box pre-filter - generous
# enough at CONUS latitudes to always contain anything within
# MAX_MATCH_DISTANCE_M.
BBOX_PAD_DEG = 0.2


async def nearest_airport_icao(session: AsyncSession, lat: float, lon: float) -> str | None:
    rows = await session.execute(
        text(
            """
            SELECT icao, lat, lon
            FROM airports
            WHERE lat BETWEEN :min_lat AND :max_lat
              AND lon BETWEEN :min_lon AND :max_lon
            """
        ),
        {
            "min_lat": lat - BBOX_PAD_DEG,
            "max_lat": lat + BBOX_PAD_DEG,
            "min_lon": lon - BBOX_PAD_DEG,
            "max_lon": lon + BBOX_PAD_DEG,
        },
    )
    point = LatLon(lat, lon)
    best_icao: str | None = None
    best_dist = float(MAX_MATCH_DISTANCE_M)
    for row in rows:
        dist = haversine_distance_m(point, LatLon(row.lat, row.lon))
        if dist <= best_dist:
            best_dist = dist
            best_icao = row.icao
    return best_icao
