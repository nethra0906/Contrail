"""Persists opened/closed flight legs (from leg_detect.py) to `flights`."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.assembler.leg_detect import ClosedLeg, OpenLeg
from services.common.models import Flight


async def write_opened_leg(session: AsyncSession, leg: OpenLeg) -> None:
    stmt = insert(Flight).values(
        flight_id=leg.flight_id,
        icao24=leg.icao24,
        origin_icao=leg.origin_icao,
        actual_dep=leg.actual_dep,
        status="airborne",
    )
    await session.execute(stmt)
    await session.commit()


async def write_closed_leg(session: AsyncSession, leg: ClosedLeg) -> None:
    stmt = (
        update(Flight)
        .where(Flight.flight_id == leg.flight_id)
        .values(actual_arr=leg.actual_arr, dest_icao=leg.dest_icao, status="landed")
    )
    await session.execute(stmt)
    await session.commit()
