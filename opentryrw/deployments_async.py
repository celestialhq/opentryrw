from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, func, or_, select

from .audit import write_audit_log_async, write_deployment_event_async
from .database import async_session_factory
from .deployments import (
    CapacityExceededError,
    CooldownActiveError,
    TerminateLockedError,
    _format_minutes,
    _mask_user_id,
    _short_id,
)
from .models import (
    CreateSessionRequest,
    DeploymentProvider,
    DeploymentStatus,
    NotificationTarget,
    Session,
    User,
)
from .notifications_async import send_telegram_notification_async
from .orm import CooldownORM, DeploymentJobORM, DeploymentORM, UserORM
from .providers import selected_provider
from .security import get_fingerprint_cooldown_until_async, set_fingerprint_cooldown_async
from .settings import settings

TERMINAL_STATUSES = {
    DeploymentStatus.terminated.value,
    DeploymentStatus.expired.value,
    DeploymentStatus.failed.value,
}

ACTIVE_STATUSES = {
    DeploymentStatus.queued.value,
    DeploymentStatus.initializing.value,
    DeploymentStatus.installing.value,
    DeploymentStatus.deploying.value,
    DeploymentStatus.ready.value,
    DeploymentStatus.terminating.value,
}

SESSION_STATUSES = ACTIVE_STATUSES | {DeploymentStatus.failed.value}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _dt_from_ms(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc)


def _ms_from_dt(value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def _deployment_to_provider_dict(deployment: DeploymentORM) -> dict[str, Any]:
    return {
        "id": deployment.id,
        "auth_session_id": deployment.auth_session_id,
        "user_id": deployment.user_id,
        "version": deployment.version,
        "provider": deployment.provider,
        "status": deployment.status,
        "url": deployment.access_url,
        "started_at": _ms_from_dt(deployment.started_at),
        "ready_at": _ms_from_dt(deployment.ready_at),
        "expires_at": _ms_from_dt(deployment.expires_at),
        "ready_notified": deployment.ready_notified,
        "terminated_notified": deployment.terminated_notified,
        "remnawave": deployment.remnawave_config_json,
        "provider_instance_id": deployment.provider_instance_id,
        "provider_region": deployment.provider_region,
        "provider_status": deployment.provider_status,
        "provider_last_checked_at": deployment.provider_last_checked_at,
        "provider_public_ip": deployment.provider_public_ip,
        "provider_public_host": deployment.provider_public_host,
        "health_started_at": _ms_from_dt(deployment.health_started_at) if deployment.health_started_at else None,
        "dns_provider": deployment.dns_provider,
        "dns_record_id": deployment.dns_record_id,
        "error_message": deployment.error_message,
    }


def _apply_provider_patch(deployment: DeploymentORM, patch: dict[str, Any]) -> None:
    field_map = {
        "provider_last_checked_at": "provider_last_checked_at",
        "provider_status": "provider_status",
        "provider_public_ip": "provider_public_ip",
        "provider_public_host": "provider_public_host",
        "health_started_at": "health_started_at",
        "dns_provider": "dns_provider",
        "dns_record_id": "dns_record_id",
        "dns_error": "error_message",
        "cloudflare_error": "error_message",
        "error_message": "error_message",
    }
    for source, target in field_map.items():
        if source in patch:
            setattr(deployment, target, patch[source])


async def get_cooldown_until_async(user: User) -> int | None:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        cooldown = await session.get(CooldownORM, user.id)
        if not cooldown:
            return None
        if cooldown.cooldown_until <= now:
            await session.delete(cooldown)
            await session.commit()
            return None
        return _ms_from_dt(cooldown.cooldown_until)


async def _set_cooldown_async(user: User, now: datetime) -> int | None:
    if settings.session_cooldown_seconds <= 0:
        async with async_session_factory() as session:
            await session.execute(delete(CooldownORM).where(CooldownORM.user_id == user.id))
            await session.commit()
        return None

    cooldown_until = now + timedelta(seconds=settings.session_cooldown_seconds)
    async with async_session_factory() as session:
        cooldown = await session.get(CooldownORM, user.id)
        if cooldown:
            cooldown.cooldown_until = cooldown_until
            cooldown.reason = "session_created"
        else:
            session.add(
                CooldownORM(
                    user_id=user.id,
                    cooldown_until=cooldown_until,
                    reason="session_created",
                )
            )
        await session.commit()
    return _ms_from_dt(cooldown_until)


async def active_instance_count_async() -> int:
    async with async_session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(DeploymentORM).where(DeploymentORM.status.in_(ACTIVE_STATUSES))
        )
        return int(count or 0)


async def capacity_snapshot_async() -> tuple[int, int | None]:
    return await active_instance_count_async(), settings.max_active_instances_value


async def capacity_line_async(active: int | None = None, limit: int | None = None) -> str:
    if active is None or limit is None:
        active, limit = await capacity_snapshot_async()
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


async def ensure_capacity_available_async() -> None:
    active, limit = await capacity_snapshot_async()
    if limit is not None and active >= limit:
        raise CapacityExceededError(active, limit)


async def enqueue_deployment_job_async(
    deployment_id: str,
    kind: str,
    *,
    next_run_at: datetime | None = None,
    max_attempts: int = 8,
    dedupe: bool = True,
) -> str:
    async with async_session_factory() as session:
        if dedupe:
            existing = (
                await session.execute(
                    select(DeploymentJobORM)
                    .where(
                        DeploymentJobORM.deployment_id == deployment_id,
                        DeploymentJobORM.kind == kind,
                        DeploymentJobORM.status.in_(("queued", "running")),
                    )
                    .order_by(DeploymentJobORM.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing:
                return existing.id

        job = DeploymentJobORM(
            id=str(uuid4()),
            deployment_id=deployment_id,
            kind=kind,
            status="queued",
            next_run_at=next_run_at or datetime.now(timezone.utc),
            max_attempts=max_attempts,
        )
        session.add(job)
        await session.commit()
        return job.id


async def create_session_async(
    auth_session_id: str,
    user: User,
    request: CreateSessionRequest,
    fingerprint_hash: str | None = None,
) -> Session:
    existing = await _active_deployment_for_user_async(auth_session_id, user)
    if existing:
        session = await normalize_session_async(auth_session_id, user, existing)
        if session:
            return session

    if fingerprint_hash:
        fingerprint_cooldown_until = await get_fingerprint_cooldown_until_async(fingerprint_hash)
        if fingerprint_cooldown_until:
            raise CooldownActiveError(fingerprint_cooldown_until)

    cooldown_until = await get_cooldown_until_async(user)
    if cooldown_until:
        raise CooldownActiveError(cooldown_until)

    await ensure_capacity_available_async()

    now = datetime.now(timezone.utc)
    deployment_id = str(uuid4())
    provider = selected_provider(request)
    ready_at = now + timedelta(
        seconds=(
            settings.deployment_ready_seconds
            if provider == DeploymentProvider.mock
            else settings.deployment_health_timeout_seconds
        )
    )

    async with async_session_factory() as session:
        user_row = await session.get(UserORM, user.id)
        if not user_row:
            session.add(
                UserORM(
                    id=user.id,
                    telegram_id=user.id,
                    telegram_username=user.username,
                    display_name=user.name,
                    avatar_initials=user.avatar_initials,
                    trust_score=0,
                    is_blocked=False,
                )
            )
        deployment = DeploymentORM(
            id=deployment_id,
            auth_session_id=auth_session_id,
            user_id=user.id,
            version=request.version.value,
            provider=provider.value,
            status=DeploymentStatus.queued.value,
            access_url=None,
            started_at=now,
            ready_at=ready_at,
            expires_at=now + timedelta(seconds=settings.session_ttl_seconds),
            ready_notified=False,
            terminated_notified=False,
            remnawave_config_json=request.remnawave.model_dump(mode="json"),
        )
        session.add(deployment)
        session.add(
            DeploymentJobORM(
                id=str(uuid4()),
                deployment_id=deployment_id,
                kind="provision",
                status="queued",
                next_run_at=now,
            )
        )
        await session.commit()

    await write_audit_log_async(
        "session.create.requested",
        actor_user_id=user.id,
        target_type="deployment",
        target_id=deployment_id,
        payload={"provider": provider.value, "version": request.version.value},
    )
    await write_deployment_event_async(
        deployment_id,
        "session.create.requested",
        payload={"auth_session_id": auth_session_id},
    )
    await _set_cooldown_async(user, now)
    if fingerprint_hash:
        await set_fingerprint_cooldown_async(user.id, fingerprint_hash, now)
    created = await _deployment_by_id_async(deployment_id)
    if not created:
        raise RuntimeError("Deployment was not persisted")
    result = await normalize_session_async(auth_session_id, user, created)
    if not result:
        raise RuntimeError("Deployment expired immediately after creation")
    return result


async def get_session_async(auth_session_id: str, user: User) -> Session | None:
    deployment = await _active_deployment_for_user_async(auth_session_id, user)
    if not deployment:
        return None
    return await normalize_session_async(auth_session_id, user, deployment)


async def terminate_session_async(auth_session_id: str, user: User) -> None:
    deployment = await _active_deployment_for_user_async(auth_session_id, user)
    if not deployment:
        return
    if _now_ms() - _ms_from_dt(deployment.started_at) < settings.terminate_lock_seconds * 1000:
        raise TerminateLockedError("Terminate is locked")
    should_enqueue = False
    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if row and row.status not in TERMINAL_STATUSES:
            row.status = DeploymentStatus.terminating.value
            should_enqueue = True
        await session.commit()
    if should_enqueue:
        await write_audit_log_async(
            "session.terminate.requested",
            actor_user_id=user.id,
            target_type="deployment",
            target_id=deployment.id,
        )
        await write_deployment_event_async(
            deployment.id,
            "session.terminate.requested",
            payload={"auth_session_id": auth_session_id},
        )
        await enqueue_deployment_job_async(deployment.id, "terminate", max_attempts=12)


async def _deployment_by_id_async(deployment_id: str) -> DeploymentORM | None:
    async with async_session_factory() as session:
        return await session.get(DeploymentORM, deployment_id)


async def _active_deployment_for_user_async(auth_session_id: str, user: User) -> DeploymentORM | None:
    active_condition = or_(
        DeploymentORM.status.in_(ACTIVE_STATUSES),
        and_(
            DeploymentORM.status == DeploymentStatus.failed.value,
            DeploymentORM.terminated_at.is_(None),
        ),
    )
    async with async_session_factory() as session:
        deployment = (
            await session.execute(
                select(DeploymentORM)
                .where(DeploymentORM.user_id == user.id, active_condition)
                .order_by(DeploymentORM.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if deployment:
            if deployment.auth_session_id != auth_session_id:
                deployment.auth_session_id = auth_session_id
                await session.commit()
                await session.refresh(deployment)
            return deployment

        deployment = (
            await session.execute(
                select(DeploymentORM).where(
                    DeploymentORM.auth_session_id == auth_session_id,
                    DeploymentORM.user_id == user.id,
                    active_condition,
                )
                .order_by(DeploymentORM.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return deployment


async def cleanup_expired_sessions_async() -> int:
    now = datetime.now(timezone.utc)
    deployment_ids: list[str] = []
    async with async_session_factory() as session:
        deployments = (
            await session.execute(
                select(DeploymentORM).where(
                    DeploymentORM.expires_at <= now,
                    DeploymentORM.status.in_(ACTIVE_STATUSES),
                )
            )
        ).scalars().all()

        for deployment in deployments:
            deployment.status = DeploymentStatus.terminating.value
            deployment_ids.append(deployment.id)
        await session.commit()

    for deployment_id in deployment_ids:
        await enqueue_deployment_job_async(
            deployment_id,
            "cleanup",
            next_run_at=now,
            max_attempts=12,
        )

    return len(deployment_ids)


async def _user_for_deployment_async(user_id: str) -> User | None:
    async with async_session_factory() as session:
        user = await session.get(UserORM, user_id)
        if not user:
            return None
        return User(
            id=user.id,
            name=user.display_name,
            username=user.telegram_username,
            avatar_initials=user.avatar_initials,
        )


async def normalize_session_async(
    auth_session_id: str,
    user: User,
    deployment: DeploymentORM,
) -> Session | None:
    now_ms = _now_ms()
    started_at = _ms_from_dt(deployment.started_at)
    ready_at = _ms_from_dt(deployment.ready_at)
    expires_at = _ms_from_dt(deployment.expires_at)
    elapsed = now_ms - started_at
    ready_span = max(1, ready_at - started_at)
    progress = max(0, min(100, round((elapsed / ready_span) * 100)))
    provider = DeploymentProvider(deployment.provider)

    if now_ms >= expires_at:
        async with async_session_factory() as session:
            row = await session.get(DeploymentORM, deployment.id)
            if row and row.status in ACTIVE_STATUSES:
                row.status = DeploymentStatus.terminating.value
            await session.commit()
        await enqueue_deployment_job_async(deployment.id, "cleanup", max_attempts=12)
        return None

    status = DeploymentStatus(deployment.status)
    if status == DeploymentStatus.queued:
        progress = 0
    elif status == DeploymentStatus.ready:
        progress = 100
    elif status == DeploymentStatus.failed:
        progress = min(progress, 100)

    return Session(
        id=deployment.id,
        version=deployment.version,
        provider=provider,
        provider_instance_id=deployment.provider_instance_id,
        status=status,
        url=deployment.access_url if status == DeploymentStatus.ready else None,
        started_at=started_at,
        ready_at=ready_at,
        expires_at=expires_at,
        progress_percent=progress,
        can_terminate=elapsed >= settings.terminate_lock_seconds * 1000,
    )


async def _notify_terminated_async(deployment: DeploymentORM, user: User) -> None:
    if deployment.terminated_notified:
        return

    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if row:
            row.terminated_notified = True
            await session.commit()

    await send_telegram_notification_async(
        NotificationTarget.operator,
        [
            f"☠️ <b>{settings.app_name}</b> instance has been terminated.",
            "",
            f"🪪 <b>Instance ID:</b> <code>{_short_id(deployment.id)}</code>",
            "",
            await capacity_line_async(),
        ],
    )
    await send_telegram_notification_async(
        NotificationTarget.user,
        [
            f"👋 <b>{settings.app_name}</b> session has been terminated.",
            "",
            f"🪪 <b>Session ID:</b> <code>{deployment.id}</code>",
            "",
            f"Thanks for using {settings.app_name}. We hope you had a great experience.",
            "",
            "🦋 Join Remnawave",
            "",
            f"To create new session, use {settings.app_name}.",
        ],
        chat_id=user.id,
    )


async def _notify_failed_async(deployment: DeploymentORM, user: User, error_message: str | None = None) -> None:
    if deployment.terminated_notified:
        return

    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if row:
            row.terminated_notified = True
            await session.commit()

    reason = escape(error_message or deployment.error_message or "Deployment health check timed out.")
    await send_telegram_notification_async(
        NotificationTarget.operator,
        [
            f"<b>{settings.app_name}</b> instance failed to deploy.",
            "",
            f"<b>Instance ID:</b> <code>{_short_id(deployment.id)}</code>",
            f"<b>User ID:</b> <code>{_mask_user_id(user.id)}</code>",
            f"<b>Error:</b> {reason}",
            "",
            await capacity_line_async(),
        ],
    )
    await send_telegram_notification_async(
        NotificationTarget.user,
        [
            f"<b>{settings.app_name}</b> session failed to deploy.",
            "",
            f"<b>Session ID:</b> <code>{deployment.id}</code>",
            "",
            "We could not finish provisioning your Remnawave instance.",
            f"<b>Error:</b> {reason}",
        ],
        chat_id=user.id,
    )


async def _notify_ready_async(deployment: DeploymentORM, user: User, now_ms: int) -> None:
    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if row:
            row.ready_notified = True
            await session.commit()

    minutes = max(1, round((now_ms - _ms_from_dt(deployment.started_at)) / 60_000))
    remaining = max(0, round((_ms_from_dt(deployment.expires_at) - now_ms) / 60_000))
    await send_telegram_notification_async(
        NotificationTarget.operator,
        [
            f"🦋 <b>{settings.app_name}</b> instance just got deployed.",
            "",
            f"🪪 <b>Instance ID:</b> <code>{_short_id(deployment.id)}</code>",
            f"🆔 <b>User ID:</b> <code>{_mask_user_id(user.id)}</code>",
            f"🚀 <b>Deployed in</b> {_format_minutes(minutes)}.",
            "",
            await capacity_line_async(),
        ],
    )
    await send_telegram_notification_async(
        NotificationTarget.user,
        [
            f"👋 <b>{settings.app_name}</b> session has been created.",
            "",
            f"🪪 <b>Session ID:</b> <code>{deployment.id}</code>",
            f"🔗 <b>Access URL:</b> {deployment.access_url}",
            "",
            f"🕘 Session will be deleted in {_format_minutes(remaining)}.",
        ],
        chat_id=user.id,
    )
