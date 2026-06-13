from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


class User(BaseModel):
    id: str
    name: str
    username: str | None = None
    avatar_initials: str = "T"


class DeploymentVersion(StrEnum):
    stable = "stable"
    dev = "dev"


class DeploymentStatus(StrEnum):
    queued = "queued"
    initializing = "initializing"
    installing = "installing"
    deploying = "deploying"
    ready = "ready"
    terminating = "terminating"
    terminated = "terminated"
    expired = "expired"
    failed = "failed"


class DeploymentProvider(StrEnum):
    mock = "mock"
    digitalocean = "digitalocean"


class AuthStatusResponse(BaseModel):
    authenticated: bool
    user: User | None = None
    telegram_auth_enabled: bool = False


class AuthResponse(BaseModel):
    authenticated: bool
    user: User | None = None


class TelegramNotificationsConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    notify_users: str = ""
    notify_nodes: str = ""
    notify_crm: str = ""
    notify_service: str = ""
    notify_tblocker: str = ""

    @model_validator(mode="after")
    def validate_enabled_config(self) -> "TelegramNotificationsConfig":
        if self.enabled and not self.bot_token:
            raise ValueError("bot_token is required when Telegram notifications are enabled")
        return self


class DocumentationConfig(BaseModel):
    enabled: bool = False


class WebhookConfig(BaseModel):
    enabled: bool = False
    url: HttpUrl | None = None
    secret_header: str = Field(default="", pattern=r"^[A-Za-z0-9]*$", max_length=256)

    @field_validator("url", mode="before")
    @classmethod
    def empty_url_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @model_validator(mode="after")
    def validate_enabled_config(self) -> "WebhookConfig":
        if not self.enabled:
            return self
        if self.url is None:
            raise ValueError("url is required when webhook is enabled")
        if len(self.secret_header) < 32:
            raise ValueError("secret_header must be at least 32 characters when webhook is enabled")
        return self


class RemnawaveEnvironmentConfig(BaseModel):
    telegram_notifications: TelegramNotificationsConfig = Field(
        default_factory=TelegramNotificationsConfig
    )
    documentation: DocumentationConfig = Field(default_factory=DocumentationConfig)
    webhook: WebhookConfig = Field(default_factory=WebhookConfig)


class CreateSessionRequest(BaseModel):
    version: DeploymentVersion = DeploymentVersion.stable
    remnawave: RemnawaveEnvironmentConfig = Field(default_factory=RemnawaveEnvironmentConfig)


class Session(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: UUID
    version: DeploymentVersion
    provider: DeploymentProvider = DeploymentProvider.mock
    provider_instance_id: str | None = None
    status: DeploymentStatus
    url: HttpUrl | None = None
    started_at: int
    ready_at: int
    expires_at: int
    progress_percent: int = Field(ge=0, le=100)
    can_terminate: bool = False


class SessionResponse(BaseModel):
    session: Session | None
    cooldown_until: int | None = None
    can_create_session: bool = True


class NotificationTarget(StrEnum):
    operator = "operator"
    user = "user"


class Notification(BaseModel):
    id: UUID
    target: NotificationTarget
    lines: list[str]
    created_at: int
    delivery_status: Literal["stored", "sent", "failed"]
    error: str | None = None


class NotificationsResponse(BaseModel):
    notifications: list[Notification]
