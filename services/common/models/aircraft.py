from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, PrimaryKeyConstraint, String
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class Aircraft(Base):
    """Static aircraft registry, keyed by 24-bit ICAO transponder address."""

    __tablename__ = "aircraft"

    icao24: Mapped[str] = mapped_column(String(6), primary_key=True)
    registration: Mapped[str | None] = mapped_column(String(16))
    type_code: Mapped[str | None] = mapped_column(String(8))
    operator_icao: Mapped[str | None] = mapped_column(String(8))
    wtc: Mapped[str | None] = mapped_column(String(1))  # L/M/H/J wake turbulence category
    first_seen: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class StateVector(Base):
    """Raw position reports. TimescaleDB hypertable - converted in a
    post-create migration step, not expressible in the ORM DDL directly.
    PK is (icao24, ts): the natural idempotency key for ADS-B redelivery.
    """

    __tablename__ = "state_vectors"
    __table_args__ = (PrimaryKeyConstraint("icao24", "ts"),)

    icao24: Mapped[str] = mapped_column(String(6))
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    baro_alt_ft: Mapped[float | None] = mapped_column(Float)
    geo_alt_ft: Mapped[float | None] = mapped_column(Float)
    velocity_kt: Mapped[float | None] = mapped_column(Float)
    heading_deg: Mapped[float | None] = mapped_column(Float)
    vert_rate_fpm: Mapped[float | None] = mapped_column(Float)
    on_ground: Mapped[bool] = mapped_column(Boolean, default=False)
    squawk: Mapped[str | None] = mapped_column(String(4))
    h3_r5: Mapped[str | None] = mapped_column(String(16))
    source: Mapped[str] = mapped_column(String(16))  # adsb_lol | airplanes_live | opensky
