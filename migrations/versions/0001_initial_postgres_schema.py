"""initial postgres schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-12
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("telegram_id", sa.String(length=64), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("telegram_first_name", sa.String(length=255), nullable=True),
        sa.Column("telegram_last_name", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("avatar_initials", sa.String(length=8), nullable=False),
        sa.Column("trust_score", sa.Integer(), nullable=False),
        sa.Column("is_blocked", sa.Boolean(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("telegram_id"),
    )
    op.create_table(
        "auth_states",
        sa.Column("state", sa.String(length=128), nullable=False),
        sa.Column("code_verifier", sa.String(length=255), nullable=False),
        sa.Column("nonce", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("state"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=True),
        sa.Column("target_id", sa.String(length=128), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("target", sa.String(length=32), nullable=False),
        sa.Column("chat_id", sa.String(length=128), nullable=True),
        sa.Column("thread_id", sa.BigInteger(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_mode", sa.String(length=32), nullable=True),
        sa.Column("delivery_status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(length=128), nullable=True),
        sa.Column("user_agent_hash", sa.String(length=128), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "cooldowns",
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )

    op.create_table(
        "deployments",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=False),
        sa.Column("auth_session_id", sa.String(length=64), nullable=True),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("access_url", sa.Text(), nullable=True),
        sa.Column("provider_instance_id", sa.String(length=128), nullable=True),
        sa.Column("provider_region", sa.String(length=64), nullable=True),
        sa.Column("provider_status", sa.String(length=64), nullable=True),
        sa.Column("provider_last_checked_at", sa.BigInteger(), nullable=True),
        sa.Column("provider_public_ip", sa.String(length=64), nullable=True),
        sa.Column("provider_public_host", sa.String(length=255), nullable=True),
        sa.Column("dns_provider", sa.String(length=64), nullable=True),
        sa.Column("dns_record_id", sa.String(length=255), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("remnawave_config_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_notified", sa.Boolean(), nullable=False),
        sa.Column("terminated_notified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployments_provider_instance", "deployments", ["provider", "provider_instance_id"], unique=False)
    op.create_index("ix_deployments_status_expires", "deployments", ["status", "expires_at"], unique=False)
    op.create_index("ix_deployments_user_status", "deployments", ["user_id", "status"], unique=False)
    op.create_index(op.f("ix_deployments_auth_session_id"), "deployments", ["auth_session_id"], unique=False)

    op.create_table(
        "abuse_signals",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.String(length=64), nullable=True),
        sa.Column("signal_type", sa.String(length=64), nullable=False),
        sa.Column("value_hash", sa.String(length=128), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_abuse_signals_user_type", "abuse_signals", ["user_id", "signal_type"], unique=False)
    op.create_index("ix_abuse_signals_value", "abuse_signals", ["signal_type", "value_hash"], unique=False)

    op.create_table(
        "provider_resources",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("deployment_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "external_id", name="uq_provider_resources_provider_external_id"),
    )

    op.create_table(
        "deployment_events",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("deployment_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "deployment_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("deployment_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_deployment_jobs_status_next_run", "deployment_jobs", ["status", "next_run_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_deployment_jobs_status_next_run", table_name="deployment_jobs")
    op.drop_table("deployment_jobs")
    op.drop_table("deployment_events")
    op.drop_table("provider_resources")
    op.drop_index("ix_abuse_signals_value", table_name="abuse_signals")
    op.drop_index("ix_abuse_signals_user_type", table_name="abuse_signals")
    op.drop_table("abuse_signals")
    op.drop_index(op.f("ix_deployments_auth_session_id"), table_name="deployments")
    op.drop_index("ix_deployments_user_status", table_name="deployments")
    op.drop_index("ix_deployments_status_expires", table_name="deployments")
    op.drop_index("ix_deployments_provider_instance", table_name="deployments")
    op.drop_table("deployments")
    op.drop_table("cooldowns")
    op.drop_table("auth_sessions")
    op.drop_table("notifications")
    op.drop_table("audit_log")
    op.drop_table("auth_states")
    op.drop_table("users")
