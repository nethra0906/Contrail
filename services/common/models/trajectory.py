from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from services.common.db import Base


class TrajectoryEmbedding(Base):
    __tablename__ = "trajectory_embeddings"

    flight_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("flights.flight_id"), primary_key=True
    )
    # Real Postgres column type is `vector(64)` (set via raw SQL in the
    # migration, see 0001_initial_schema.py) - mapped here as Text because the
    # `pgvector` Python package isn't a project dependency. Any code that
    # later writes to this column must format the value as a pgvector literal
    # string (e.g. "[0.1,0.2,...]").
    embedding: Mapped[str | None] = mapped_column(Text)
