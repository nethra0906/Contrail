"""sim_events surrogate primary key

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sim_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
    )
    op.create_primary_key("pk_sim_events", "sim_events", ["id"])


def downgrade() -> None:
    op.drop_constraint("pk_sim_events", "sim_events", type_="primary")
    op.drop_column("sim_events", "id")
