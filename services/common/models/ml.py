from __future__ import annotations

import datetime as dt

from sqlalchemy import Boolean, DateTime, Float, Integer, PrimaryKeyConstraint, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class Prediction(Base):
    """A forecast issued by a model. Compared against ground truth later by
    the scoring job to populate PredictionScore - that join is the live
    model scorecard's only data source.
    """

    __tablename__ = "predictions"
    __table_args__ = (PrimaryKeyConstraint("icao24", "issued_at", "horizon_s", "model_version"),)

    icao24: Mapped[str] = mapped_column(String(6))
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    horizon_s: Mapped[int] = mapped_column(Integer)
    pred_lat: Mapped[float] = mapped_column(Float)
    pred_lon: Mapped[float] = mapped_column(Float)
    pred_alt_ft: Mapped[float | None] = mapped_column(Float)
    sigma_h_km: Mapped[float] = mapped_column(Float)
    q10_lat: Mapped[float | None] = mapped_column(Float)
    q10_lon: Mapped[float | None] = mapped_column(Float)
    q90_lat: Mapped[float | None] = mapped_column(Float)
    q90_lon: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str] = mapped_column(String(64))


class PredictionScore(Base):
    __tablename__ = "prediction_scores"
    __table_args__ = (PrimaryKeyConstraint("icao24", "issued_at", "horizon_s", "model_version"),)

    icao24: Mapped[str] = mapped_column(String(6))
    issued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    horizon_s: Mapped[int] = mapped_column(Integer)
    error_km: Mapped[float] = mapped_column(Float)
    crps: Mapped[float | None] = mapped_column(Float)
    in_80pct_interval: Mapped[bool | None] = mapped_column(Boolean)
    phase: Mapped[str | None] = mapped_column(String(16))
    model_version: Mapped[str] = mapped_column(String(64))


class ModelRegistry(Base):
    __tablename__ = "model_registry"

    model_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))  # trajectory | eta | delay_gnn | anomaly
    trained_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))
    train_window: Mapped[dict] = mapped_column(JSONB)
    metrics: Mapped[dict] = mapped_column(JSONB)
    artifact_uri: Mapped[str] = mapped_column(String(256))
    promoted: Mapped[bool] = mapped_column(Boolean, default=False)
