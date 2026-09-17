"""Stage 2 Definition of Done, as a real test against a real TimescaleDB
container: the ingest write path is idempotent on (icao24, ts), and the
bbox query the live map polls returns only the latest position per aircraft
inside the box. Requires Docker - see tests/integration/conftest.py.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from services.common.schemas.aircraft import StateVectorIn
from services.ingest.main import write_batch


def _sv(icao24: str, lat: float, lon: float, ts: dt.datetime) -> StateVectorIn:
    return StateVectorIn(
        icao24=icao24, ts=ts, lat=lat, lon=lon, velocity_kt=250.0, on_ground=False, source="test"
    )


async def test_write_batch_is_idempotent_on_icao24_ts(db_engine, monkeypatch):
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("services.ingest.main.get_sessionmaker", lambda: sessionmaker)

    ts = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    record = _sv("abc123", 40.0, -74.0, ts)

    written_first = await write_batch([record])
    written_second = await write_batch([record])  # same (icao24, ts) again

    assert written_first == 1
    assert written_second == 1  # upsert, not a duplicate-key failure

    async with sessionmaker() as session:
        count = (
            await session.execute(
                text("SELECT count(*) FROM state_vectors WHERE icao24 = 'abc123'")
            )
        ).scalar_one()
    assert count == 1  # exactly one row despite two writes


async def test_write_batch_upsert_keeps_latest_values(db_engine, monkeypatch):
    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("services.ingest.main.get_sessionmaker", lambda: sessionmaker)

    ts = dt.datetime(2026, 1, 1, 12, tzinfo=dt.UTC)
    stale = _sv("def456", 40.0, -74.0, ts)
    fresher = _sv("def456", 40.5, -74.5, ts)  # same key, different position

    await write_batch([stale])
    await write_batch([fresher])

    async with sessionmaker() as session:
        row = (
            await session.execute(
                text("SELECT lat, lon FROM state_vectors WHERE icao24 = 'def456'")
            )
        ).one()
    assert row.lat == 40.5
    assert row.lon == -74.5


async def test_bbox_query_returns_only_latest_per_aircraft(db_engine, monkeypatch):
    from services.api.routers.aircraft import list_aircraft

    sessionmaker = async_sessionmaker(db_engine, expire_on_commit=False)
    monkeypatch.setattr("services.ingest.main.get_sessionmaker", lambda: sessionmaker)

    now = dt.datetime.now(dt.UTC)
    older = _sv("ghi789", 40.0, -74.0, now - dt.timedelta(seconds=10))
    newer = _sv("ghi789", 40.1, -74.1, now - dt.timedelta(seconds=1))
    outside_bbox = _sv("jkl012", 10.0, 10.0, now)

    await write_batch([older, newer, outside_bbox])

    async with sessionmaker() as session:
        results = await list_aircraft(
            min_lat=39.0, max_lat=41.0, min_lon=-75.0, max_lon=-73.0, limit=100, session=session
        )

    matching = [r for r in results if r["icao24"] == "ghi789"]
    assert len(matching) == 1
    assert matching[0]["lat"] == 40.1  # the newer report, not the older one
    assert not any(r["icao24"] == "jkl012" for r in results)  # outside bbox excluded
