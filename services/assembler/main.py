"""Assembler service entrypoint (Stage 3): consumes `adsb.raw`, runs the
track-state machine and leg detector on each aircraft's stream, fans live
deltas out to Redis for `/ws/live`, and persists opened/closed flight legs
- the pieces `pipeline.py`, `track_state.py`, and `leg_detect.py` decide,
kept pure and unit-tested; this module is just the I/O around them.

Single replica for now (see AssemblerState's docstring for why that's fine
at this stage). Offsets are committed manually, after the sink writes for a
message succeed, matching the at-least-once + idempotent-writes contract in
the spec's Kafka topics table.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from services.assembler.pipeline import (
    AssemblerState,
    advance_track,
    close_leg_if_landing,
    open_leg_if_takeoff,
)
from services.assembler.sinks.flights import write_closed_leg, write_opened_leg
from services.assembler.sinks.live_fanout import publish_delta
from services.assembler.sinks.nearest_airport import nearest_airport_icao
from services.common.bus import TOPIC_ADSB_RAW, TOPIC_FLIGHTS_EVENTS, make_consumer, make_producer
from services.common.cache import get_redis
from services.common.config import get_settings
from services.common.db import get_sessionmaker
from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import CONSUMER_LAG, configure_logging, get_logger

logger = get_logger(__name__)

CONSUMER_GROUP = "assembler"


async def handle_message(
    payload: dict,
    state: AssemblerState,
    redis_client,
    sessionmaker,
    events_producer,
) -> None:
    sv = StateVectorIn(**{k: v for k, v in payload.items() if k != "h3_r5"})
    h3_cell = payload.get("h3_r5")

    track = advance_track(sv, state)

    if h3_cell:
        await publish_delta(redis_client, sv, h3_cell)

    if not (track.just_took_off or track.just_landed):
        return

    async with sessionmaker() as session:
        nearest = await nearest_airport_icao(session, sv.lat, sv.lon)

        opened = open_leg_if_takeoff(track, state, nearest)
        if opened is not None:
            await write_opened_leg(session, opened)
            await events_producer.send_and_wait(
                TOPIC_FLIGHTS_EVENTS,
                value={
                    "type": "takeoff",
                    "flight_id": str(opened.flight_id),
                    "icao24": opened.icao24,
                },
                key=str(opened.flight_id).encode(),
            )

        closed, rejected = close_leg_if_landing(track, state, nearest)
        if closed is not None:
            await write_closed_leg(session, closed)
            await events_producer.send_and_wait(
                TOPIC_FLIGHTS_EVENTS,
                value={
                    "type": "landing",
                    "flight_id": str(closed.flight_id),
                    "icao24": closed.icao24,
                },
                key=str(closed.flight_id).encode(),
            )
        elif rejected:
            logger.info("landing_without_open_leg", icao24=track.icao24, ts=str(track.last_ts))


async def run() -> None:
    configure_logging()
    get_settings()

    consumer = await make_consumer(TOPIC_ADSB_RAW, group_id=CONSUMER_GROUP)
    events_producer = await make_producer()
    redis_client = get_redis()
    sessionmaker = get_sessionmaker()
    state = AssemblerState()
    stop_event = asyncio.Event()

    def _handle_signal() -> None:
        logger.info("shutdown_signal_received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal)

    logger.info("assembler_starting")
    try:
        while not stop_event.is_set():
            batches = await consumer.getmany(timeout_ms=1000)
            for tp, messages in batches.items():
                for msg in messages:
                    try:
                        await handle_message(
                            msg.value, state, redis_client, sessionmaker, events_producer
                        )
                    except Exception:
                        logger.exception("assembler_message_failed", icao24=msg.value.get("icao24"))
                        continue
                if messages:
                    await consumer.commit({tp: messages[-1].offset + 1})
                    highwater = consumer.highwater(tp) or 0
                    CONSUMER_LAG.labels(topic=tp.topic, group=CONSUMER_GROUP).set(
                        max(0, highwater - (messages[-1].offset + 1))
                    )
    finally:
        await consumer.stop()
        await events_producer.stop()
        logger.info("assembler_stopped")


if __name__ == "__main__":
    asyncio.run(run())
