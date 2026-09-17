from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class Anomaly(Base):
    __tablename__ = "anomalies"
    __table_args__ = (Index("ix_anomalies_ts_score", "ts", "score"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    flight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flights.flight_id")
    )
    icao24: Mapped[str] = mapped_column(String(6), index=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    kind: Mapped[str] = mapped_column(
        String(32)
    )  # squawk_emergency | rapid_descent | go_around | holding | learned
    score: Mapped[float] = mapped_column(Float)
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
