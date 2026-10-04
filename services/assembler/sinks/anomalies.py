"""Persists anomaly-rules detections (anomaly_rules.py) to `anomalies`."""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from services.common.models import Anomaly
from services.inference.anomaly_rules import AnomalyEvent


async def write_anomaly(
    session: AsyncSession,
    event: AnomalyEvent,
    icao24: str,
    ts: dt.datetime,
    flight_id: uuid.UUID | None = None,
) -> None:
    stmt = insert(Anomaly).values(
        id=uuid.uuid4(),
        flight_id=flight_id,
        icao24=icao24,
        ts=ts,
        kind=event.kind.value,
        score=event.score,
        evidence=event.evidence,
    )
    await session.execute(stmt)
    await session.commit()
