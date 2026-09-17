from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class Flight(Base):
    """One flight leg. `prev_leg_flight_id` is the aircraft-rotation edge that
    lets delay propagate from an inbound aircraft's late arrival to its next
    scheduled departure - the mechanism the M3 GNN and the DES layer both rely
    on for realistic cascades.
    """

    __tablename__ = "flights"
    __table_args__ = (
        Index("ix_flights_origin_sched_dep", "origin_icao", "sched_dep"),
        Index("ix_flights_dest_sched_arr", "dest_icao", "sched_arr"),
        Index(
            "ix_flights_status_airborne",
            "status",
            postgresql_where=text("status = 'airborne'"),
        ),
    )

    flight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    icao24: Mapped[str | None] = mapped_column(String(6), index=True)
    callsign: Mapped[str | None] = mapped_column(String(8))
    origin_icao: Mapped[str | None] = mapped_column(String(4), ForeignKey("airports.icao"))
    dest_icao: Mapped[str | None] = mapped_column(String(4), ForeignKey("airports.icao"))
    carrier: Mapped[str | None] = mapped_column(String(4))
    tail_number: Mapped[str | None] = mapped_column(String(16), index=True)

    sched_dep: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    actual_dep: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    sched_arr: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    actual_arr: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    status: Mapped[str] = mapped_column(String(16), default="scheduled")
    dep_delay_min: Mapped[int | None] = mapped_column(Integer)
    arr_delay_min: Mapped[int | None] = mapped_column(Integer)
    diverted: Mapped[bool] = mapped_column(Boolean, default=False)
    cancelled: Mapped[bool] = mapped_column(Boolean, default=False)

    prev_leg_flight_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flights.flight_id")
    )
