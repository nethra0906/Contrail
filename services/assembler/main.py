"""Assembler service entrypoint (Stage 3): consumes `adsb.raw`, runs the
track-state machine and leg detector on each aircraft's stream, fans live
deltas out to Redis for `/ws/live`, archives to Parquet, publishes the
OpenAP/phase-enriched `adsb.enriched` record, and persists opened/closed
flight legs - the pieces `pipeline.py`, `track_state.py`, `leg_detect.py`,
`enrich.py`, and `sampling.py` decide, kept pure and unit-tested; this
module is just the I/O around them.

Single replica for now (see AssemblerState's docstring for why that's fine
at this stage). Offsets are committed manually, after the sink writes for a
message succeed, matching the at-least-once + idempotent-writes contract in
the spec's Kafka topics table.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

from services.assembler.enrich import estimate_fuel_flow_kg_s
from services.assembler.pipeline import (
    AssemblerState,
    advance_track,
    close_leg_if_landing,
    open_leg_if_takeoff,
)
from services.assembler.sampling import should_sample
from services.assembler.sinks.aircraft_registry import type_code_for
from services.assembler.sinks.anomalies import write_anomaly
from services.assembler.sinks.enriched import publish_enriched
from services.assembler.sinks.flights import write_closed_leg, write_opened_leg
from services.assembler.sinks.live_fanout import publish_delta
from services.assembler.sinks.nearest_airport import nearest_airport_icao
from services.assembler.sinks.parquet import ParquetBuffer, flush_rows
from services.common.bus import TOPIC_ADSB_RAW, TOPIC_FLIGHTS_EVENTS, make_consumer, make_producer
from services.common.cache import get_redis
from services.common.config import get_settings
from services.common.db import get_sessionmaker
from services.common.schemas.aircraft import StateVectorIn
from services.common.telemetry import (
    ANOMALIES_DETECTED_TOTAL,
    CONSUMER_LAG,
    configure_logging,
    get_logger,
)
from services.inference.anomaly_rules import check_all_rules

logger = get_logger(__name__)

CONSUMER_GROUP = "assembler"


async def handle_message(
    payload: dict,
    state: AssemblerState,
    redis_client,
    sessionmaker,
    events_producer,
    parquet_buffer: ParquetBuffer,
) -> None:
    sv = StateVectorIn(**{k: v for k, v in payload.items() if k != "h3_r5"})
    h3_cell = payload.get("h3_r5")

    previous_track = state.tracks.get(sv.icao24)
    track = advance_track(sv, state)

    # Adaptive sampling (sampling.py): thins the live fanout and Parquet
    # sinks during steady cruise, never the Kafka log below - adsb.raw is
    # already written by ingest, and adsb.enriched (published lower down)
    # stays a complete record for the same reason.
    sampled = should_sample(track.phase, state.last_sampled_ts.get(sv.icao24), sv.ts)
    if sampled:
        state.last_sampled_ts[sv.icao24] = sv.ts
        if h3_cell:
            await publish_delta(redis_client, sv, h3_cell)
        parquet_buffer.add(sv, h3_cell)

    if sv.icao24 not in state.known_types:
        async with sessionmaker() as session:
            await type_code_for(session, sv.icao24, state.known_types)

    fuel_flow_kg_s = estimate_fuel_flow_kg_s(
        state.known_types.get(sv.icao24),
        sv.on_ground,
        sv.baro_alt_ft,
        sv.velocity_kt,
        sv.vert_rate_fpm,
    )
    open_leg = state.open_legs.get(sv.icao24)

    anomaly_events = check_all_rules(sv, previous_track, track)
    if anomaly_events:
        async with sessionmaker() as session:
            for event in anomaly_events:
                await write_anomaly(
                    session,
                    event,
                    sv.icao24,
                    sv.ts,
                    flight_id=open_leg.flight_id if open_leg else None,
                )
        for event in anomaly_events:
            ANOMALIES_DETECTED_TOTAL.labels(kind=event.kind.value).inc()

    await publish_enriched(
        events_producer,
        sv,
        track.phase,
        fuel_flow_kg_s,
        open_leg.origin_icao if open_leg else None,
    )

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
    parquet_buffer = ParquetBuffer()
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
                            msg.value,
                            state,
                            redis_client,
                            sessionmaker,
                            events_producer,
                            parquet_buffer,
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

            # Parquet is a secondary, catch-up-able sink (see parquet.py's
            # docstring) - flushed off the event loop so a slow object-store
            # write never stalls message consumption or offset commits.
            if parquet_buffer.should_flush():
                await asyncio.to_thread(flush_rows, parquet_buffer.take())
    finally:
        await asyncio.to_thread(flush_rows, parquet_buffer.take())
        await consumer.stop()
        await events_producer.stop()
        logger.info("assembler_stopped")


if __name__ == "__main__":
    asyncio.run(run())
