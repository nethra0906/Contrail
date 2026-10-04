from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class WeatherObs(Base):
    __tablename__ = "weather_obs"

    station: Mapped[str] = mapped_column(String(8), primary_key=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    wind_dir: Mapped[float | None] = mapped_column(Float)
    wind_kt: Mapped[float | None] = mapped_column(Float)
    gust_kt: Mapped[float | None] = mapped_column(Float)
    vis_sm: Mapped[float | None] = mapped_column(Float)
    ceiling_ft: Mapped[float | None] = mapped_column(Float)
    wx_codes: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    raw: Mapped[str | None] = mapped_column(Text)
