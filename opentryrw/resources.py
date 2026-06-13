from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from .database import async_session_factory
from .orm import ProviderResourceORM


async def upsert_provider_resource_async(
    *,
    deployment_id: str,
    provider: str,
    external_id: str | None,
    kind: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if not external_id:
        return

    async with async_session_factory() as session:
        resource = (
            await session.execute(
                select(ProviderResourceORM).where(
                    ProviderResourceORM.provider == provider,
                    ProviderResourceORM.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if resource:
            resource.deployment_id = deployment_id
            resource.kind = kind
            resource.metadata_json = metadata or {}
            resource.deleted_at = None
        else:
            session.add(
                ProviderResourceORM(
                    deployment_id=deployment_id,
                    provider=provider,
                    external_id=external_id,
                    kind=kind,
                    metadata_json=metadata or {},
                )
            )
        await session.commit()


async def mark_provider_resource_deleted_async(
    *,
    provider: str,
    external_id: str | None,
) -> None:
    if not external_id:
        return

    async with async_session_factory() as session:
        resource = (
            await session.execute(
                select(ProviderResourceORM).where(
                    ProviderResourceORM.provider == provider,
                    ProviderResourceORM.external_id == external_id,
                )
            )
        ).scalar_one_or_none()
        if resource:
            resource.deleted_at = datetime.now(timezone.utc)
            await session.commit()
