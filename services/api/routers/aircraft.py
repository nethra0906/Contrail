from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.db import get_session

router = APIRouter(prefix="/aircraft", tags=["aircraft"])

# Recency window for "currently visible" aircraft - anything older than this
# is considered stale (feed dropout, aircraft landed and stopped squawking).
LIVE_STALENESS_SECONDS = 30


@router.get("")
async def list_aircraft(
    min_lat: float = Query(..., ge=-90, le=90),
    max_lat: float = Query(..., ge=-90, le=90),
    min_lon: float = Query(..., ge=-180, le=180),
    max_lon: float = Query(..., ge=-180, le=180),
    limit: int = Query(2000, ge=1, le=10000),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Latest known position for every aircraft currently inside the bbox,
    using DISTINCT ON over the most recent staleness window - this is the
    query the live map polls before Stage 3 replaces it with the WS feed.
    """
    if min_lat > max_lat or min_lon > max_lon:
        raise HTTPException(400, "bbox min must be <= max")

    query = text(
        """
        SELECT DISTINCT ON (icao24)
            icao24, ts, lat, lon, baro_alt_ft, velocity_kt, heading_deg,
            vert_rate_fpm, on_ground
        FROM state_vectors
        WHERE ts > now() - make_interval(secs => :staleness)
          AND lat BETWEEN :min_lat AND :max_lat
          AND lon BETWEEN :min_lon AND :max_lon
        ORDER BY icao24, ts DESC
        LIMIT :limit
        """
    )
    rows = await session.execute(
        query,
        {
            "staleness": LIVE_STALENESS_SECONDS,
            "min_lat": min_lat,
            "max_lat": max_lat,
            "min_lon": min_lon,
            "max_lon": max_lon,
            "limit": limit,
        },
    )
    return [dict(row._mapping) for row in rows]


@router.get("/{icao24}")
async def get_aircraft(icao24: str, session: AsyncSession = Depends(get_session)) -> dict:
    query = text(
        """
        SELECT icao24, ts, lat, lon, baro_alt_ft, velocity_kt, heading_deg,
               vert_rate_fpm, on_ground, squawk
        FROM state_vectors
        WHERE icao24 = :icao24
        ORDER BY ts DESC
        LIMIT 1
        """
    )
    row = (await session.execute(query, {"icao24": icao24.lower()})).first()
    if row is None:
        raise HTTPException(404, f"no recent state for {icao24}")
    return dict(row._mapping)


@router.get("/{icao24}/track")
async def get_track(
    icao24: str,
    from_: dt.datetime | None = Query(None, alias="from"),
    to: dt.datetime | None = Query(None),
    limit: int = Query(2000, ge=1, le=20000),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    from_ = from_ or (dt.datetime.now(dt.UTC) - dt.timedelta(hours=1))
    to = to or dt.datetime.now(dt.UTC)
    if from_ > to:
        raise HTTPException(400, "from must be <= to")

    query = text(
        """
        SELECT icao24, ts, lat, lon, baro_alt_ft, velocity_kt, heading_deg, vert_rate_fpm, on_ground
        FROM state_vectors
        WHERE icao24 = :icao24 AND ts BETWEEN :from_ AND :to
        ORDER BY ts ASC
        LIMIT :limit
        """
    )
    rows = await session.execute(
        query, {"icao24": icao24.lower(), "from_": from_, "to": to, "limit": limit}
    )
    return [dict(row._mapping) for row in rows]
