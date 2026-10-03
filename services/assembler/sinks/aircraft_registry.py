"""In-process cache for `aircraft.type_code`, used to decide whether OpenAP
fuel enrichment (enrich.py) is possible for a given icao24. A DB round trip
per state vector would be wasteful at up to a few Hz per aircraft - the
type_code is static for an airframe's lifetime, so one miss-then-cache
lookup per process is all this needs. Like AssemblerState's other in-memory
maps, the cache is dropped on a process restart - acceptable at this stage
(see pipeline.py's AssemblerState docstring).
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def type_code_for(
    session: AsyncSession, icao24: str, cache: dict[str, str | None]
) -> str | None:
    if icao24 in cache:
        return cache[icao24]

    row = (
        await session.execute(
            text("SELECT type_code FROM aircraft WHERE icao24 = :icao24"),
            {"icao24": icao24},
        )
    ).first()
    type_code = row.type_code if row is not None else None
    cache[icao24] = type_code
    return type_code
