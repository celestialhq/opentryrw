from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, or_, select

from .audit import write_audit_log_async, write_deployment_event_async
from .database import async_session_factory, close_database
from .deployments_async import (
    ACTIVE_STATUSES,
    SESSION_STATUSES,
    _apply_provider_patch,
    _deployment_to_provider_dict,
    _ms_from_dt,
    _notify_failed_async,
    _notify_ready_async,
    _notify_terminated_async,
    _user_for_deployment_async,
    enqueue_deployment_job_async,
)
from .models import (
    CreateSessionRequest,
    DeploymentProvider,
    DeploymentStatus,
    DeploymentVersion,
    RemnawaveEnvironmentConfig,
)
from .orm import DeploymentJobORM, DeploymentORM, ProviderResourceORM
from .providers import ProviderError, destroy_provider, provision_provider, refresh_provider
from .resources import mark_provider_resource_deleted_async, upsert_provider_resource_async
from .settings import settings

LOGGER = logging.getLogger("opentryrw.worker")

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"

JOB_PROVISION = "provision"
JOB_REFRESH = "refresh"
JOB_TERMINATE = "terminate"
JOB_CLEANUP = "cleanup"


async def run_worker(stop_event: asyncio.Event | None = None) -> None:
    if settings.storage_backend != "postgres":
        raise RuntimeError("The deployment worker requires STORAGE_BACKEND=postgres")

    LOGGER.info("starting worker id=%s", settings.worker_id)
    local_stop = stop_event or asyncio.Event()
    next_reconcile_at = datetime.now(timezone.utc)
    try:
        while not local_stop.is_set():
            now = datetime.now(timezone.utc)
            if now >= next_reconcile_at:
                await reconcile_once()
                next_reconcile_at = now + timedelta(seconds=settings.reconciliation_interval_seconds)
            processed = await run_once()
            timeout = 0 if processed else settings.worker_poll_interval_seconds
            try:
                await asyncio.wait_for(local_stop.wait(), timeout=timeout)
            except TimeoutError:
                continue
    finally:
        await close_database()


async def run_once() -> int:
    job_ids = await claim_jobs()
    for job_id in job_ids:
        await process_job(job_id)
    return len(job_ids)


async def claim_jobs() -> list[str]:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=settings.worker_lock_seconds)
    async with async_session_factory() as session:
        async with session.begin():
            jobs = (
                await session.execute(
                    select(DeploymentJobORM)
                    .where(
                        DeploymentJobORM.next_run_at <= now,
                        or_(
                            DeploymentJobORM.status == JOB_QUEUED,
                            and_(
                                DeploymentJobORM.status == JOB_RUNNING,
                                DeploymentJobORM.locked_at < stale_before,
                            ),
                        ),
                    )
                    .order_by(DeploymentJobORM.next_run_at.asc(), DeploymentJobORM.created_at.asc())
                    .limit(settings.worker_batch_size)
                    .with_for_update(skip_locked=True)
                )
            ).scalars().all()
            job_ids: list[str] = []
            for job in jobs:
                job.status = JOB_RUNNING
                job.locked_at = now
                job.locked_by = settings.worker_id
                job.attempts += 1
                job_ids.append(job.id)
            return job_ids


async def process_job(job_id: str) -> None:
    try:
        async with async_session_factory() as session:
            job = await session.get(DeploymentJobORM, job_id)
            if not job:
                return
            deployment = await session.get(DeploymentORM, job.deployment_id)
            if not deployment:
                await mark_job_done(job_id)
                return
            kind = job.kind

        if kind == JOB_PROVISION:
            await process_provision(job_id, deployment)
        elif kind == JOB_REFRESH:
            await process_refresh(job_id, deployment)
        elif kind == JOB_TERMINATE:
            await process_destroy(job_id, deployment, final_status=DeploymentStatus.terminated)
        elif kind == JOB_CLEANUP:
            await process_destroy(job_id, deployment, final_status=DeploymentStatus.expired)
        else:
            raise RuntimeError(f"Unknown deployment job kind: {kind}")
    except Exception as exc:
        LOGGER.exception("deployment job failed: %s", job_id)
        await reschedule_or_fail_job(job_id, exc)


async def process_provision(job_id: str, deployment: DeploymentORM) -> None:
    if deployment.status not in ACTIVE_STATUSES:
        await mark_job_done(job_id)
        return

    provider_data: dict[str, Any] = {}
    if deployment.provider_instance_id or deployment.access_url:
        provider_data = {}
    else:
        request = request_from_deployment(deployment)
        provider_data = await asyncio.to_thread(
            provision_provider,
            deployment.id,
            deployment.user_id,
            request,
        )

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if not row or row.status not in ACTIVE_STATUSES:
            await mark_job_done(job_id)
            return
        apply_provider_data(row, provider_data)
        row.status = DeploymentStatus.initializing.value
        await session.commit()

    await track_provider_resource_async(deployment, provider_data)
    await write_deployment_event_async(
        deployment.id,
        "provider.provisioned",
        payload={
            "provider": deployment.provider,
            "provider_instance_id": provider_data.get("provider_instance_id"),
        },
    )
    await write_audit_log_async(
        "provider.provisioned",
        actor_user_id=deployment.user_id,
        target_type="deployment",
        target_id=deployment.id,
        payload={"provider": deployment.provider},
    )
    next_refresh = now + timedelta(
        seconds=1 if deployment.provider == DeploymentProvider.mock.value else settings.provider_refresh_interval_seconds
    )
    await enqueue_deployment_job_async(deployment.id, JOB_REFRESH, next_run_at=next_refresh)
    await mark_job_done(job_id)


async def process_refresh(job_id: str, deployment: DeploymentORM) -> None:
    now = datetime.now(timezone.utc)
    if deployment.status not in SESSION_STATUSES:
        await mark_job_done(job_id)
        return
    if deployment.status == DeploymentStatus.failed.value:
        await process_failed_deployment(job_id, deployment)
        return
    if deployment.expires_at <= now:
        async with async_session_factory() as session:
            row = await session.get(DeploymentORM, deployment.id)
            if row and row.status in ACTIVE_STATUSES:
                row.status = DeploymentStatus.terminating.value
                await session.commit()
        await enqueue_deployment_job_async(deployment.id, JOB_CLEANUP, max_attempts=12)
        await mark_job_done(job_id)
        return

    if deployment.provider == DeploymentProvider.mock.value:
        refresh = mock_refresh(deployment)
    else:
        refresh = await asyncio.to_thread(refresh_provider, _deployment_to_provider_dict(deployment))

    status = refresh.status or DeploymentStatus(deployment.status)
    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if not row:
            await mark_job_done(job_id)
            return
        if refresh.provider_patch:
            _apply_provider_patch(row, refresh.provider_patch)
        if refresh.url:
            row.access_url = refresh.url
        row.status = status.value
        await session.commit()
        await session.refresh(row)
        deployment = row

    user = await _user_for_deployment_async(deployment.user_id)
    if user and status == DeploymentStatus.ready and not deployment.ready_notified:
        await _notify_ready_async(deployment, user, now_ms())
        await write_deployment_event_async(deployment.id, "session.ready")

    if status == DeploymentStatus.terminated:
        if user and not deployment.terminated_notified:
            await _notify_terminated_async(deployment, user)
        await mark_job_done(job_id)
        return

    if status in {DeploymentStatus.ready, DeploymentStatus.failed}:
        if status == DeploymentStatus.failed:
            await write_deployment_event_async(
                deployment.id,
                "session.failed",
                payload={"error_message": deployment.error_message},
            )
            await process_failed_deployment(job_id, deployment)
            return
        await mark_job_done(job_id)
        return

    await enqueue_deployment_job_async(
        deployment.id,
        JOB_REFRESH,
        next_run_at=now + timedelta(seconds=settings.provider_refresh_interval_seconds),
        dedupe=False,
    )
    await mark_job_done(job_id)


async def process_failed_deployment(job_id: str, deployment: DeploymentORM) -> None:
    if deployment.terminated_at:
        await mark_job_done(job_id)
        return

    user = await _user_for_deployment_async(deployment.user_id)
    if user and not deployment.terminated_notified:
        await _notify_failed_async(deployment, user, deployment.error_message)

    if deployment.provider_instance_id or deployment.dns_record_id or deployment.dns_provider:
        await asyncio.to_thread(destroy_provider, _deployment_to_provider_dict(deployment))
        await mark_provider_resource_deleted_async(
            provider=deployment.provider,
            external_id=deployment.provider_instance_id,
        )

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if row:
            row.status = DeploymentStatus.failed.value
            row.terminated_at = now
            await session.commit()

    await write_audit_log_async(
        "session.failed",
        actor_user_id=deployment.user_id,
        target_type="deployment",
        target_id=deployment.id,
        payload={"provider": deployment.provider, "error_message": deployment.error_message},
    )
    await mark_job_done(job_id)


async def process_destroy(
    job_id: str,
    deployment: DeploymentORM,
    *,
    final_status: DeploymentStatus,
) -> None:
    if deployment.status == final_status.value and deployment.terminated_at:
        user = await _user_for_deployment_async(deployment.user_id)
        if user and not deployment.terminated_notified:
            await _notify_terminated_async(deployment, user)
        await mark_job_done(job_id)
        return

    if deployment.provider_instance_id or deployment.dns_record_id or deployment.dns_provider:
        await asyncio.to_thread(destroy_provider, _deployment_to_provider_dict(deployment))
    await mark_provider_resource_deleted_async(
        provider=deployment.provider,
        external_id=deployment.provider_instance_id,
    )

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        row = await session.get(DeploymentORM, deployment.id)
        if not row:
            await mark_job_done(job_id)
            return
        row.status = final_status.value
        row.terminated_at = now
        await session.commit()
        await session.refresh(row)
        deployment = row

    user = await _user_for_deployment_async(deployment.user_id)
    if user and not deployment.terminated_notified:
        await _notify_terminated_async(deployment, user)
    await write_deployment_event_async(
        deployment.id,
        f"session.{final_status.value}",
        payload={"provider": deployment.provider, "provider_instance_id": deployment.provider_instance_id},
    )
    await write_audit_log_async(
        f"session.{final_status.value}",
        actor_user_id=deployment.user_id,
        target_type="deployment",
        target_id=deployment.id,
        payload={"provider": deployment.provider},
    )
    await mark_job_done(job_id)


async def mark_job_done(job_id: str) -> None:
    async with async_session_factory() as session:
        job = await session.get(DeploymentJobORM, job_id)
        if job:
            job.status = JOB_DONE
            job.locked_at = None
            job.locked_by = None
            job.last_error = None
            await session.commit()


async def reschedule_or_fail_job(job_id: str, exc: Exception) -> None:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        job = await session.get(DeploymentJobORM, job_id)
        if not job:
            return
        job.last_error = str(exc)
        job.locked_at = None
        job.locked_by = None
        if job.attempts >= job.max_attempts:
            job.status = JOB_FAILED
            deployment = await session.get(DeploymentORM, job.deployment_id)
            if deployment and job.kind in {JOB_PROVISION, JOB_REFRESH}:
                deployment.status = DeploymentStatus.failed.value
                deployment.error_message = str(exc)
        else:
            retry_seconds = min(300, 2 ** min(job.attempts, 8))
            job.status = JOB_QUEUED
            job.next_run_at = now + timedelta(seconds=retry_seconds)
        await session.commit()
    await write_audit_log_async(
        "deployment.job.failed" if job.attempts >= job.max_attempts else "deployment.job.retry",
        target_type="deployment_job",
        target_id=job_id,
        payload={"error": str(exc)},
    )


async def track_provider_resource_async(
    deployment: DeploymentORM,
    provider_data: dict[str, Any],
) -> None:
    external_id = provider_data.get("provider_instance_id") or deployment.provider_instance_id
    if not external_id:
        return
    await upsert_provider_resource_async(
        deployment_id=deployment.id,
        provider=deployment.provider,
        external_id=str(external_id),
        kind="instance",
        metadata={
            "region": provider_data.get("provider_region") or deployment.provider_region,
            "status": provider_data.get("provider_status") or deployment.provider_status,
            "public_host": provider_data.get("provider_public_host") or deployment.provider_public_host,
        },
    )


async def reconcile_once() -> int:
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(seconds=settings.stale_resource_grace_seconds)
    enqueued = 0
    async with async_session_factory() as session:
        recoverable_statuses = (
            DeploymentStatus.queued.value,
            DeploymentStatus.initializing.value,
            DeploymentStatus.installing.value,
            DeploymentStatus.deploying.value,
            DeploymentStatus.terminating.value,
            DeploymentStatus.failed.value,
        )
        recoverable_deployments = (
            await session.execute(
                select(DeploymentORM).where(
                    DeploymentORM.status.in_(recoverable_statuses),
                    DeploymentORM.expires_at > now,
                    or_(
                        DeploymentORM.status != DeploymentStatus.failed.value,
                        DeploymentORM.terminated_at.is_(None),
                    ),
                )
            )
        ).scalars().all()
        active_jobs = (
            await session.execute(
                select(DeploymentJobORM.deployment_id).where(
                    DeploymentJobORM.status.in_((JOB_QUEUED, JOB_RUNNING))
                )
            )
        ).scalars().all()
        active_job_deployment_ids = set(active_jobs)

        stuck_deployments = (
            await session.execute(
                select(DeploymentORM).where(
                    DeploymentORM.status.in_(
                        (
                            DeploymentStatus.queued.value,
                            DeploymentStatus.initializing.value,
                            DeploymentStatus.installing.value,
                            DeploymentStatus.deploying.value,
                            DeploymentStatus.failed.value,
                        )
                    ),
                    DeploymentORM.expires_at > now,
                    DeploymentORM.updated_at <= stale_before,
                    or_(
                        DeploymentORM.status != DeploymentStatus.failed.value,
                        DeploymentORM.terminated_at.is_(None),
                    ),
                )
            )
        ).scalars().all()

        stale_deployments = (
            await session.execute(
                select(DeploymentORM).where(
                    DeploymentORM.status.in_(
                        (
                            DeploymentStatus.terminated.value,
                            DeploymentStatus.expired.value,
                        )
                    ),
                    DeploymentORM.provider_instance_id.is_not(None),
                    DeploymentORM.terminated_at.is_(None),
                    DeploymentORM.updated_at <= stale_before,
                )
            )
        ).scalars().all()

        resources = (
            await session.execute(
                select(ProviderResourceORM).where(
                    ProviderResourceORM.deleted_at.is_(None),
                    ProviderResourceORM.deployment_id.is_not(None),
                    ProviderResourceORM.created_at <= stale_before,
                )
            )
        ).scalars().all()
        resource_deployment_ids = [resource.deployment_id for resource in resources if resource.deployment_id]

    for deployment in recoverable_deployments:
        if deployment.id in active_job_deployment_ids:
            continue
        kind = JOB_REFRESH
        if deployment.status == DeploymentStatus.queued.value:
            kind = JOB_PROVISION
        elif deployment.status == DeploymentStatus.terminating.value:
            kind = JOB_TERMINATE
        await enqueue_deployment_job_async(
            deployment.id,
            kind,
            next_run_at=now,
            max_attempts=12,
        )
        enqueued += 1

    for deployment in stuck_deployments:
        await enqueue_deployment_job_async(
            deployment.id,
            JOB_PROVISION if deployment.status == DeploymentStatus.queued.value else JOB_REFRESH,
            next_run_at=now,
            max_attempts=12,
        )
        enqueued += 1

    for deployment in stale_deployments:
        await enqueue_deployment_job_async(
            deployment.id,
            JOB_CLEANUP,
            next_run_at=now,
            max_attempts=12,
        )
        enqueued += 1

    for deployment_id in resource_deployment_ids:
        deployment = await deployment_by_id(deployment_id)
        if not deployment:
            continue
        if deployment.status in {
            DeploymentStatus.terminated.value,
            DeploymentStatus.expired.value,
        }:
            await enqueue_deployment_job_async(
                deployment.id,
                JOB_CLEANUP,
                next_run_at=now,
                max_attempts=12,
            )
            enqueued += 1

    if enqueued:
        await write_audit_log_async(
            "reconciliation.cleanup.enqueued",
            payload={"count": enqueued},
        )
    return enqueued


async def deployment_by_id(deployment_id: str) -> DeploymentORM | None:
    async with async_session_factory() as session:
        return await session.get(DeploymentORM, deployment_id)


def request_from_deployment(deployment: DeploymentORM) -> CreateSessionRequest:
    return CreateSessionRequest(
        version=DeploymentVersion(deployment.version),
        remnawave=RemnawaveEnvironmentConfig.model_validate(
            deployment.remnawave_config_json or {}
        ),
    )


def apply_provider_data(deployment: DeploymentORM, provider_data: dict[str, Any]) -> None:
    if not provider_data:
        return
    if "url" in provider_data:
        deployment.access_url = provider_data["url"]
    field_map = {
        "provider_instance_id": "provider_instance_id",
        "provider_region": "provider_region",
        "provider_status": "provider_status",
        "provider_last_checked_at": "provider_last_checked_at",
        "provider_public_ip": "provider_public_ip",
        "provider_public_host": "provider_public_host",
        "dns_provider": "dns_provider",
        "dns_record_id": "dns_record_id",
        "error_message": "error_message",
    }
    for source, target in field_map.items():
        if source in provider_data:
            setattr(deployment, target, provider_data[source])


def mock_refresh(deployment: DeploymentORM):
    from .providers.base import ProviderRefresh

    elapsed_ms = now_ms() - _ms_from_dt(deployment.started_at)
    if elapsed_ms >= settings.deployment_ready_seconds * 1000:
        return ProviderRefresh(status=DeploymentStatus.ready, progress_percent=100, url=deployment.access_url)
    if elapsed_ms >= 18_000:
        return ProviderRefresh(status=DeploymentStatus.deploying, progress_percent=70, url=deployment.access_url)
    if elapsed_ms >= 9_000:
        return ProviderRefresh(status=DeploymentStatus.installing, progress_percent=35, url=deployment.access_url)
    return ProviderRefresh(status=DeploymentStatus.initializing, progress_percent=10, url=deployment.access_url)


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        LOGGER.info("worker stopped")
    except ProviderError:
        raise


if __name__ == "__main__":
    main()
