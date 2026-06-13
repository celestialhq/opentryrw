"""add deployment health window timestamp

Revision ID: 0003_health_started_at
Revises: 0002_rate_limits
Create Date: 2026-06-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0003_health_started_at"
down_revision = "0002_rate_limits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "deployments",
        sa.Column("health_started_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("deployments", "health_started_at")
