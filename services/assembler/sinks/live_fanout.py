"""Publishes live delta frames to Redis pub/sub, one channel per H3 cell, so
`/ws/live` only has to subscribe to the cells a client's viewport covers -
"never broadcast the whole world" per the WS protocol spec.
"""

from __future__ import annotations

import redis.asyncio as redis

from services.common.schemas.aircraft import StateVectorIn
from services.common.ws_protocol import FRAME_TYPE_DELTA, AircraftRecord, encode_frame


def live_channel(h3_cell: str) -> str:
    return f"live:cell:{h3_cell}"


async def publish_delta(redis_client: redis.Redis, sv: StateVectorIn, h3_cell: str) -> None:
    record = AircraftRecord(
        icao24=sv.icao24,
        lat=sv.lat,
        lon=sv.lon,
        alt_ft=sv.baro_alt_ft,
        heading_deg=sv.heading_deg,
        vert_rate_fpm=sv.vert_rate_fpm,
        velocity_kt=sv.velocity_kt,
        on_ground=sv.on_ground,
    )
    frame = encode_frame([record], FRAME_TYPE_DELTA, ts_delta_ms=0)
    await redis_client.publish(live_channel(h3_cell), frame)
