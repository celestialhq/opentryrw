from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UserORM(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    telegram_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    telegram_first_name: Mapped[str | None] = mapped_column(String(255))
    telegram_last_name: Mapped[str | None] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_initials: Mapped[str] = mapped_column(String(8), nullable=False, default="T")
    trust_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    auth_sessions: Mapped[list[AuthSessionORM]] = relationship(back_populates="user")
    deployments: Mapped[list[DeploymentORM]] = relationship(back_populates="user")


class AuthSessionORM(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip_hash: Mapped[str | None] = mapped_column(String(128))
    user_agent_hash: Mapped[str | None] = mapped_column(String(128))

    user: Mapped[UserORM] = relationship(back_populates="auth_sessions")


class AuthStateORM(Base):
    __tablename__ = "auth_states"

    state: Mapped[str] = mapped_column(String(128), primary_key=True)
    code_verifier: Mapped[str] = mapped_column(String(255), nullable=False)
    nonce: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeploymentORM(Base, TimestampMixin):
    __tablename__ = "deployments"
    __table_args__ = (
        Index("ix_deployments_user_status", "user_id", "status"),
        Index("ix_deployments_provider_instance", "provider", "provider_instance_id"),
        Index("ix_deployments_status_expires", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    auth_session_id: Mapped[str | None] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    access_url: Mapped[str | None] = mapped_column(Text)
    provider_instance_id: Mapped[str | None] = mapped_column(String(128))
    provider_region: Mapped[str | None] = mapped_column(String(64))
    provider_status: Mapped[str | None] = mapped_column(String(64))
    provider_last_checked_at: Mapped[int | None] = mapped_column(BigInteger)
    provider_public_ip: Mapped[str | None] = mapped_column(String(64))
    provider_public_host: Mapped[str | None] = mapped_column(String(255))
    dns_provider: Mapped[str | None] = mapped_column(String(64))
    dns_record_id: Mapped[str | None] = mapped_column(String(255))
    error_message: Mapped[str | None] = mapped_column(Text)
    remnawave_config_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    health_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ready_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    terminated_notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped[UserORM] = relationship(back_populates="deployments")
    jobs: Mapped[list[DeploymentJobORM]] = relationship(back_populates="deployment")
    events: Mapped[list[DeploymentEventORM]] = relationship(back_populates="deployment")


class DeploymentJobORM(Base, TimestampMixin):
    __tablename__ = "deployment_jobs"
    __table_args__ = (Index("ix_deployment_jobs_status_next_run", "status", "next_run_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    last_error: Mapped[str | None] = mapped_column(Text)

    deployment: Mapped[DeploymentORM] = relationship(back_populates="jobs")


class DeploymentEventORM(Base):
    __tablename__ = "deployment_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    deployment_id: Mapped[str] = mapped_column(ForeignKey("deployments.id", ondelete="CASCADE"), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    deployment: Mapped[DeploymentORM] = relationship(back_populates="events")


class CooldownORM(Base):
    __tablename__ = "cooldowns"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))


class NotificationORM(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    target: Mapped[str] = mapped_column(String(32), nullable=False)
    chat_id: Mapped[str | None] = mapped_column(String(128))
    thread_id: Mapped[int | None] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[str | None] = mapped_column(String(32))
    delivery_status: Mapped[str] = mapped_column(String(32), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AbuseSignalORM(Base):
    __tablename__ = "abuse_signals"
    __table_args__ = (
        Index("ix_abuse_signals_user_type", "user_id", "signal_type"),
        Index("ix_abuse_signals_value", "signal_type", "value_hash"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    signal_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RateLimitORM(Base):
    __tablename__ = "rate_limits"
    __table_args__ = (Index("ix_rate_limits_expires_at", "expires_at"),)

    key: Mapped[str] = mapped_column(String(255), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ProviderResourceORM(Base):
    __tablename__ = "provider_resources"
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_provider_resources_provider_external_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    deployment_id: Mapped[str | None] = mapped_column(ForeignKey("deployments.id", ondelete="SET NULL"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLogORM(Base):
    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    actor_user_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64))
    target_id: Mapped[str | None] = mapped_column(String(128))
    payload_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
