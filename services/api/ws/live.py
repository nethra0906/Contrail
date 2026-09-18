"""`/ws/live`: binary aircraft feed.

Client sends one `{"op": "subscribe", "h3_cells": [...]}` message right
after connecting; the server replies with a full snapshot frame scoped to
those cells (queried from `state_vectors`, same recency window the REST bbox
endpoint uses), then forwards delta frames from the assembler's Redis
pub/sub fanout (see `services/assembler/sinks/live_fanout.py`) as they
arrive - never the whole world, only the client's requested cells.

Changing viewport currently means reconnecting with a new subscribe message
rather than resubscribing mid-stream - redis-py's PubSub object isn't safe
to drive from two concurrent coroutines (one reading control messages, one
listening for deltas) without added locking, and reconnecting is cheap
enough for now that it isn't worth that complexity yet.
"""

from __future__ import annotations

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import text

from services.assembler.sinks.live_fanout import live_channel
from services.common.cache import get_redis
from services.common.db import get_sessionmaker
from services.common.telemetry import WS_CONNECTED_CLIENTS, get_logger
from services.common.ws_protocol import FRAME_TYPE_FULL, AircraftRecord, encode_frame

router = APIRouter(tags=["live"])
logger = get_logger(__name__)

# A client panning across the entire CONUS bbox at H3 resolution 5 covers a
# few hundred cells - cap the subscription so a pathological client can't
# force the server to fan out (and this connection to receive) everything.
MAX_SUBSCRIBED_CELLS = 300
LIVE_STALENESS_SECONDS = 30


async def build_full_frame(h3_cells: list[str]) -> bytes:
    if not h3_cells:
        return encode_frame([], FRAME_TYPE_FULL, ts_delta_ms=0)

    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        rows = await session.execute(
            text(
                """
                SELECT DISTINCT ON (icao24)
                    icao24, lat, lon, baro_alt_ft, heading_deg, vert_rate_fpm,
                    velocity_kt, on_ground
                FROM state_vectors
                WHERE ts > now() - make_interval(secs => :staleness)
                  AND h3_r5 = ANY(:cells)
                ORDER BY icao24, ts DESC
                """
            ),
            {"staleness": LIVE_STALENESS_SECONDS, "cells": h3_cells},
        )
        records = [
            AircraftRecord(
                icao24=row.icao24,
                lat=row.lat,
                lon=row.lon,
                alt_ft=row.baro_alt_ft,
                heading_deg=row.heading_deg,
                vert_rate_fpm=row.vert_rate_fpm,
                velocity_kt=row.velocity_kt,
                on_ground=row.on_ground,
            )
            for row in rows
        ]
    return encode_frame(records, FRAME_TYPE_FULL, ts_delta_ms=0)


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    await websocket.accept()
    WS_CONNECTED_CLIENTS.inc()
    redis_client = get_redis()
    pubsub = redis_client.pubsub()

    try:
        msg = await websocket.receive_json()
        cells: list[str] = []
        if msg.get("op") == "subscribe":
            cells = list(dict.fromkeys(msg.get("h3_cells") or []))[:MAX_SUBSCRIBED_CELLS]

        if cells:
            await pubsub.subscribe(*[live_channel(c) for c in cells])

        await websocket.send_bytes(await build_full_frame(cells))

        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_bytes(message["data"])
    except WebSocketDisconnect:
        logger.info("ws_live_disconnected")
    finally:
        WS_CONNECTED_CLIENTS.dec()
        await pubsub.close()
