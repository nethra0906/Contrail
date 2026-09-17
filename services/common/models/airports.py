from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class Airport(Base):
    __tablename__ = "airports"

    icao: Mapped[str] = mapped_column(String(4), primary_key=True)
    iata: Mapped[str | None] = mapped_column(String(3))
    name: Mapped[str] = mapped_column(String(128))
    city: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(2))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    elevation_ft: Mapped[float | None] = mapped_column(Float)
    tz: Mapped[str | None] = mapped_column(String(64))
    hub_rank: Mapped[int | None] = mapped_column(Integer)  # by annual ops, populated from BTS


class Runway(Base):
    __tablename__ = "runways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    airport_icao: Mapped[str] = mapped_column(String(4), ForeignKey("airports.icao"), index=True)
    ident: Mapped[str] = mapped_column(String(8))  # e.g. "22L"
    true_heading_deg: Mapped[float | None] = mapped_column(Float)
    length_ft: Mapped[float | None] = mapped_column(Float)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
