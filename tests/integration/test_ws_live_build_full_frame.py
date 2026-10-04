"""`services/api/ws/live.py`'s `build_full_frame` against a real Postgres
container - requires Docker, see tests/integration/conftest.py. This is
the query+encode path a client gets on connect/subscribe; it had no test
coverage before this (only the pure `encode_frame`/`decode_frame` round
trip in tests/unit/test_ws_protocol.py was covered).
"""

from __future__ import annotations

import datetime as dt

from services.api.ws import live as live_module
from services.common.config import get_settings
from services.common.models import StateVector
from services.common.ws_protocol import FRAME_TYPE_FULL, decode_frame

CELL_A = "85283473fffffff"
CELL_B = "85283447fffffff"


def _sv(icao24: str, ts: dt.datetime, lat: float, lon: float, h3_r5: str) -> StateVector:
    return StateVector(
        icao24=icao24,
        ts=ts,
        lat=lat,
        lon=lon,
        baro_alt_ft=35000.0,
        geo_alt_ft=35200.0,
        velocity_kt=420.0,
        heading_deg=270.0,
        vert_rate_fpm=0.0,
        on_ground=False,
        squawk="1200",
        h3_r5=h3_r5,
        source="test",
    )


async def test_build_full_frame_returns_only_requested_cells(
    monkeypatch, db_sessionmaker, db_session
):
    monkeypatch.setattr(live_module, "get_sessionmaker", lambda: db_sessionmaker)

    now = dt.datetime.now(dt.UTC)
    db_session.add_all(
        [
            _sv("aaaaaa", now, 40.0, -74.0, CELL_A),
            _sv("bbbbbb", now, 41.0, -75.0, CELL_B),  # different cell - must be excluded
        ]
    )
    await db_session.commit()

    frame = await live_module.build_full_frame([CELL_A])
    _, frame_type, _, records = decode_frame(frame)

    assert frame_type == FRAME_TYPE_FULL
    assert [r.icao24 for r in records] == ["aaaaaa"]


async def test_build_full_frame_excludes_stale_rows(monkeypatch, db_sessionmaker, db_session):
    monkeypatch.setattr(live_module, "get_sessionmaker", lambda: db_sessionmaker)

    stale = dt.datetime.now(dt.UTC) - dt.timedelta(
        seconds=get_settings().live_staleness_seconds + 60
    )
    db_session.add(_sv("cccccc", stale, 40.0, -74.0, CELL_A))
    await db_session.commit()

    frame = await live_module.build_full_frame([CELL_A])
    _, _, _, records = decode_frame(frame)

    assert records == []


async def test_build_full_frame_returns_only_the_latest_report_per_aircraft(
    monkeypatch, db_sessionmaker, db_session
):
    monkeypatch.setattr(live_module, "get_sessionmaker", lambda: db_sessionmaker)

    now = dt.datetime.now(dt.UTC)
    earlier = now - dt.timedelta(seconds=5)
    db_session.add_all(
        [
            _sv("dddddd", earlier, 30.0, -90.0, CELL_A),
            _sv("dddddd", now, 31.0, -91.0, CELL_A),  # same aircraft, newer report
        ]
    )
    await db_session.commit()

    frame = await live_module.build_full_frame([CELL_A])
    _, _, _, records = decode_frame(frame)

    assert len(records) == 1
    assert round(records[0].lat, 3) == 31.0


async def test_build_full_frame_with_no_cells_returns_an_empty_frame():
    frame = await live_module.build_full_frame([])
    _, frame_type, _, records = decode_frame(frame)
    assert frame_type == FRAME_TYPE_FULL
    assert records == []
