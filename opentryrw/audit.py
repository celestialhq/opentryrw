from __future__ import annotations

from typing import Any

from .database import async_session_factory
from .orm import AuditLogORM, DeploymentEventORM


async def write_audit_log_async(
    action: str,
    *,
    actor_user_id: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    async with async_session_factory() as session:
        session.add(
            AuditLogORM(
                actor_user_id=actor_user_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                payload_json=payload or {},
            )
        )
        await session.commit()


async def write_deployment_event_async(
    deployment_id: str,
    event_type: str,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    async with async_session_factory() as session:
        session.add(
            DeploymentEventORM(
                deployment_id=deployment_id,
                event_type=event_type,
                payload_json=payload or {},
            )
        )
        await session.commit()
