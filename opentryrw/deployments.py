from __future__ import annotations

import time
from uuid import uuid4

from .db import Q, database
from .models import (
    CreateSessionRequest,
    DeploymentProvider,
    DeploymentStatus,
    NotificationTarget,
    Session,
    User,
)
from .notifications import send_telegram_notification
from .providers import (
    ProviderError,
    destroy_provider,
    provision_provider,
    refresh_provider,
    selected_provider,
)
from .settings import settings


class CooldownActiveError(Exception):
    def __init__(self, cooldown_until: int) -> None:
        self.cooldown_until = cooldown_until
        super().__init__("Session cooldown is active")


class TerminateLockedError(Exception):
    pass


class CapacityExceededError(Exception):
    def __init__(self, active: int, limit: int) -> None:
        self.active = active
        self.limit = limit
        super().__init__("Service capacity is full")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _short_id(value: str) -> str:
    return f"{value[:4]}...{value[-4:]}" if len(value) > 12 else value


def _mask_user_id(value: str) -> str:
    if len(value) < 3:
        return "***"
    return f"{value[:1]}{'*' * max(4, len(value) - 2)}{value[-1:]}"


def _format_minutes(value: int) -> str:
    return f"{value} {'minute' if value == 1 else 'minutes'}"


def active_instance_count() -> int:
    return sum(
        1
        for deployment in database.deployments.all()
        if deployment.get("status") not in {
            DeploymentStatus.terminated.value,
            DeploymentStatus.expired.value,
            DeploymentStatus.failed.value,
        }
    )


def capacity_snapshot() -> tuple[int, int | None]:
    return active_instance_count(), settings.max_active_instances_value


def capacity_line(active: int | None = None, limit: int | None = None) -> str:
    if active is None or limit is None:
        active, limit = capacity_snapshot()
    if limit is None:
        icon = "🟡" if active == 0 else "🟢"
        return f"{icon} {active}/∞ ({active} инстансов поднято)"
    if active >= limit:
        icon = "🔴"
    elif active == 0:
        icon = "🟡"
    else:
        icon = "🟢"
    return f"{icon} {active}/{limit} ({active} инстансов из {limit} поднято)"


def ensure_capacity_available() -> None:
    active, limit = capacity_snapshot()
    if limit is not None and active >= limit:
        raise CapacityExceededError(active, limit)


def get_cooldown_until(user: User) -> int | None:
    cooldown = database.cooldowns.get(Q.user_id == user.id)
    if not cooldown:
        return None

    cooldown_until = int(cooldown["cooldown_until"])
    if cooldown_until <= _now_ms():
        database.cooldowns.remove(Q.user_id == user.id)
        return None
    return cooldown_until


def _set_cooldown(user: User, now: int) -> int | None:
    if settings.session_cooldown_seconds <= 0:
        database.cooldowns.remove(Q.user_id == user.id)
        return None

    cooldown_until = now + settings.session_cooldown_seconds * 1000
    document = {"user_id": user.id, "cooldown_until": cooldown_until}
    if database.cooldowns.get(Q.user_id == user.id):
        database.cooldowns.update(document, Q.user_id == user.id)
    else:
        database.cooldowns.insert(document)
    return cooldown_until


def create_session(auth_session_id: str, user: User, request: CreateSessionRequest) -> Session:
    existing = _active_deployment_for_user(auth_session_id, user)
    if existing:
        session = normalize_session(auth_session_id, user, existing)
        if session:
            return session

    cooldown_until = get_cooldown_until(user)
    if cooldown_until:
        raise CooldownActiveError(cooldown_until)

    ensure_capacity_available()

    now = _now_ms()
    deployment_id = str(uuid4())
    provider = selected_provider(request)
    provider_data = provision_provider(deployment_id, user.id, request)
    deployment = {
        "id": deployment_id,
        "auth_session_id": auth_session_id,
        "user_id": user.id,
        "version": request.version.value,
        "provider": provider.value,
        "status": DeploymentStatus.initializing.value,
        "url": provider_data.pop("url", None),
        "started_at": now,
        "ready_at": now
        + (
            settings.deployment_ready_seconds
            if provider == DeploymentProvider.mock
            else settings.deployment_health_timeout_seconds
        )
        * 1000,
        "expires_at": now + settings.session_ttl_seconds * 1000,
        "ready_notified": False,
        "terminated_notified": False,
        "remnawave": request.remnawave.model_dump(mode="json"),
        **provider_data,
    }
    database.deployments.remove(Q.user_id == user.id)
    database.deployments.insert(deployment)
    _set_cooldown(user, now)
    return normalize_session(auth_session_id, user, deployment)


def get_session(auth_session_id: str, user: User) -> Session | None:
    deployment = _active_deployment_for_user(auth_session_id, user)
    if not deployment:
        return None
    return normalize_session(auth_session_id, user, deployment)


def terminate_session(auth_session_id: str, user: User) -> None:
    deployment = _active_deployment_for_user(auth_session_id, user)
    if deployment:
        if _now_ms() - deployment["started_at"] < settings.terminate_lock_seconds * 1000:
            raise TerminateLockedError("Terminate is locked")
        destroy_provider(deployment)
        _notify_terminated(deployment, user)
        database.deployments.remove(Q.id == deployment["id"])


def _active_deployment_for_user(auth_session_id: str, user: User) -> dict | None:
    deployment = database.deployments.get(Q.user_id == user.id)
    if deployment:
        if deployment.get("auth_session_id") != auth_session_id:
            database.deployments.update({"auth_session_id": auth_session_id}, Q.id == deployment["id"])
            deployment = {**deployment, "auth_session_id": auth_session_id}
        return deployment

    deployment = database.deployments.get(Q.auth_session_id == auth_session_id)
    if deployment and deployment.get("user_id") == user.id:
        return deployment
    return None


def cleanup_expired_sessions() -> int:
    now = _now_ms()
    removed = 0

    for deployment in list(database.deployments.all()):
        if now < deployment["expires_at"]:
            continue

        user_document = database.users.get(Q.id == deployment["user_id"])
        if user_document:
            try:
                destroy_provider(deployment)
            except ProviderError as exc:
                database.deployments.update({"error_message": str(exc)}, Q.id == deployment["id"])
                continue
            _notify_terminated(deployment, User.model_validate(user_document))
        database.deployments.remove(Q.id == deployment["id"])
        removed += 1

    return removed


def _notify_terminated(deployment: dict, user: User) -> None:
    if deployment.get("terminated_notified"):
        return

    database.deployments.update({"terminated_notified": True}, Q.id == deployment["id"])
    send_telegram_notification(
        NotificationTarget.operator,
        [
            f"☠️ <b>{settings.app_name}</b> instance has been terminated.",
            "",
            f"🪪 <b>Instance ID:</b> <code>{_short_id(deployment['id'])}</code>",
            "",
            capacity_line(),
        ],
    )
    send_telegram_notification(
        NotificationTarget.user,
        [
            f"👋 <b>{settings.app_name}</b> session has been terminated.",
            "",
            f"🪪 <b>Session ID:</b> <code>{deployment['id']}</code>",
            "",
            f"Thanks for using {settings.app_name}. We hope you had a great experience.",
            "",
            "🦋 Join Remnawave",
            "",
            f"To create new session, use {settings.app_name}.",
        ],
        chat_id=user.id,
    )


def normalize_session(auth_session_id: str, user: User, deployment: dict) -> Session | None:
    now = _now_ms()
    elapsed = now - deployment["started_at"]
    ready_span = max(1, deployment["ready_at"] - deployment["started_at"])
    progress = max(0, min(100, round((elapsed / ready_span) * 100)))
    provider = DeploymentProvider(deployment.get("provider", DeploymentProvider.mock.value))

    if now >= deployment["expires_at"]:
        destroy_provider(deployment)
        _notify_terminated(deployment, user)
        database.deployments.remove(Q.id == deployment["id"])
        return None

    if provider == DeploymentProvider.mock:
        if now >= deployment["ready_at"]:
            status = DeploymentStatus.ready
            progress = 100
        elif elapsed >= 18_000:
            status = DeploymentStatus.deploying
        elif elapsed >= 9_000:
            status = DeploymentStatus.installing
        else:
            status = DeploymentStatus.initializing
    else:
        refresh = refresh_provider(deployment)
        if refresh.provider_patch:
            deployment = {**deployment, **refresh.provider_patch}
        if refresh.url:
            deployment["url"] = refresh.url
        if refresh.status == DeploymentStatus.terminated:
            database.deployments.remove(Q.id == deployment["id"])
            return None
        status = refresh.status or DeploymentStatus(deployment["status"])
        progress = refresh.progress_percent if refresh.progress_percent is not None else progress

    database.deployments.update(
        {
            "status": status.value,
            "url": deployment.get("url"),
            "provider_last_checked_at": deployment.get("provider_last_checked_at"),
            "provider_status": deployment.get("provider_status"),
            "provider_public_ip": deployment.get("provider_public_ip"),
            "provider_public_host": deployment.get("provider_public_host"),
            "dns_provider": deployment.get("dns_provider"),
            "dns_record_id": deployment.get("dns_record_id"),
            "dns_error": deployment.get("dns_error"),
            "cloudflare_dns_record_id": deployment.get("cloudflare_dns_record_id"),
            "cloudflare_error": deployment.get("cloudflare_error"),
            "error_message": deployment.get("error_message"),
        },
        Q.id == deployment["id"],
    )

    if status == DeploymentStatus.ready and not deployment.get("ready_notified"):
        _notify_ready(deployment, user, now)

    return Session(
        id=deployment["id"],
        version=deployment["version"],
        provider=provider,
        provider_instance_id=deployment.get("provider_instance_id"),
        status=status,
        url=deployment["url"] if status == DeploymentStatus.ready else None,
        started_at=deployment["started_at"],
        ready_at=deployment["ready_at"],
        expires_at=deployment["expires_at"],
        progress_percent=progress,
        can_terminate=elapsed >= settings.terminate_lock_seconds * 1000,
    )


def _notify_ready(deployment: dict, user: User, now: int) -> None:
    database.deployments.update({"ready_notified": True}, Q.id == deployment["id"])
    minutes = max(1, round((now - deployment["started_at"]) / 60_000))
    remaining = max(0, round((deployment["expires_at"] - now) / 60_000))
    send_telegram_notification(
        NotificationTarget.operator,
        [
            f"🦋 <b>{settings.app_name}</b> instance just got deployed.",
            "",
            f"🪪 <b>Instance ID:</b> <code>{_short_id(deployment['id'])}</code>",
            f"🆔 <b>User ID:</b> <code>{_mask_user_id(user.id)}</code>",
            f"🚀 <b>Deployed in</b> {_format_minutes(minutes)}.",
            "",
            capacity_line(),
        ],
    )
    send_telegram_notification(
        NotificationTarget.user,
        [
            f"👋 <b>{settings.app_name}</b> session has been created.",
            "",
            f"🪪 <b>Session ID:</b> <code>{deployment['id']}</code>",
            f"🔗 <b>Access URL:</b> {deployment['url']}",
            "",
            f"🕘 Session will be deleted in {_format_minutes(remaining)}.",
        ],
        chat_id=user.id,
    )
