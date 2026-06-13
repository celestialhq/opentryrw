"""add rate limit counters

Revision ID: 0002_rate_limits
Revises: 0001_initial
Create Date: 2026-06-13
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0002_rate_limits"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_index("ix_rate_limits_expires_at", "rate_limits", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limits_expires_at", table_name="rate_limits")
    op.drop_table("rate_limits")
