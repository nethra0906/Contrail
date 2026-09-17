from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class Snapshot(Base):
    """A materialized world-state at a point in time - the fork point for a
    counterfactual run. `kafka_offsets` records exactly where in the replay
    log this snapshot was taken, so a run can be audited back to raw data.
    """

    __tablename__ = "snapshots"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ts: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), index=True)
    kafka_offsets: Mapped[dict] = mapped_column(JSONB)
    blob_uri: Mapped[str] = mapped_column(String(256))
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class ScenarioRow(Base):
    __tablename__ = "scenarios"

    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    spec: Mapped[dict] = mapped_column(JSONB)
    spec_hash: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True))


class SimRun(Base):
    __tablename__ = "sim_runs"
    __table_args__ = (Index("ix_sim_runs_status_lease", "status", "lease_expires_at"),)

    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("snapshots.snapshot_id")
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.scenario_id")
    )
    seed: Mapped[int] = mapped_column(BigInteger)
    model_versions: Mapped[dict] = mapped_column(JSONB)
    # queued | running | done | failed | partial
    status: Mapped[str] = mapped_column(String(16), default="queued")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(String(512))
    worker_id: Mapped[str | None] = mapped_column(String(64))
    lease_expires_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
