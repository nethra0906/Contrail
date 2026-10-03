"""Publishes the `adsb.enriched` Kafka record: the raw state vector plus the
live assembler's phase classification, the origin airport of the aircraft's
in-progress leg (if any - a free lookup from in-memory state, no DB call),
and an OpenAP fuel-flow estimate (if the aircraft's type is known). This is
the "enriched" half of Stage 3's ingest -> assemble -> enrich pipeline
described in the master spec's Kafka topics table.
"""

from __future__ import annotations

from aiokafka import AIOKafkaProducer

from services.assembler.track_state import Phase
from services.common.bus import TOPIC_ADSB_ENRICHED
from services.common.schemas.aircraft import StateVectorIn


async def publish_enriched(
    producer: AIOKafkaProducer,
    sv: StateVectorIn,
    phase: Phase,
    fuel_flow_kg_s: float | None,
    origin_icao: str | None,
) -> None:
    payload = sv.model_dump(mode="json")
    payload["phase"] = phase.value
    payload["fuel_flow_kg_s"] = fuel_flow_kg_s
    payload["origin_icao"] = origin_icao
    await producer.send_and_wait(TOPIC_ADSB_ENRICHED, value=payload, key=sv.icao24.encode())
