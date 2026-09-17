from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.db import get_session
from services.common.models import Airport, Runway

router = APIRouter(prefix="/airports", tags=["airports"])


@router.get("")
async def list_airports(session: AsyncSession = Depends(get_session)) -> list[dict]:
    rows = (await session.execute(select(Airport))).scalars().all()
    return [
        {
            "icao": a.icao,
            "iata": a.iata,
            "name": a.name,
            "city": a.city,
            "lat": a.lat,
            "lon": a.lon,
            "hub_rank": a.hub_rank,
        }
        for a in rows
    ]


@router.get("/{icao}")
async def get_airport(icao: str, session: AsyncSession = Depends(get_session)) -> dict:
    airport = await session.get(Airport, icao.upper())
    if airport is None:
        raise HTTPException(404, f"unknown airport {icao}")
    runways = (
        (await session.execute(select(Runway).where(Runway.airport_icao == icao.upper())))
        .scalars()
        .all()
    )
    return {
        "icao": airport.icao,
        "iata": airport.iata,
        "name": airport.name,
        "city": airport.city,
        "lat": airport.lat,
        "lon": airport.lon,
        "elevation_ft": airport.elevation_ft,
        "runways": [
            {"ident": r.ident, "heading_deg": r.true_heading_deg, "length_ft": r.length_ft}
            for r in runways
        ],
    }
