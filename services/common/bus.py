"""Kafka (Redpanda) producer/consumer factories.

Every producer here sets acks="all" and a stable message key so that
partitioning is deterministic per-entity (icao24 for state vectors, flight_id
for flight/anomaly/sim events) - that's what lets a consumer commit offsets
per-key-ordered and makes replay-by-offset-seek meaningful.
"""

from __future__ import annotations

import orjson
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from services.common.config import get_settings


def _serialize(value: dict) -> bytes:
    return orjson.dumps(value)


def _deserialize(raw: bytes) -> dict:
    return orjson.loads(raw)


async def make_producer() -> AIOKafkaProducer:
    settings = get_settings()
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        value_serializer=_serialize,
        acks="all",
        enable_idempotence=True,
        linger_ms=20,
    )
    await producer.start()
    return producer


async def make_consumer(*topics: str, group_id: str) -> AIOKafkaConsumer:
    settings = get_settings()
    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=group_id,
        value_deserializer=_deserialize,
        enable_auto_commit=False,  # commit only after a successful sink write
        auto_offset_reset="earliest",
    )
    await consumer.start()
    return consumer


# Topic names - the single registry so a typo doesn't create a stray topic.
TOPIC_ADSB_RAW = "adsb.raw"
TOPIC_ADSB_ENRICHED = "adsb.enriched"
TOPIC_FLIGHTS_EVENTS = "flights.events"
TOPIC_ANOMALIES_DETECTED = "anomalies.detected"
TOPIC_SIM_JOBS = "sim.jobs"


def sim_frames_topic(run_id: str) -> str:
    return f"sim.frames.{run_id}"
