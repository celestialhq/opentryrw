from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx

from .database import async_session_factory
from .models import Notification, NotificationTarget
from .notifications import notification_destination
from .orm import NotificationORM
from .settings import settings


async def send_telegram_notification_async(
    target: NotificationTarget,
    lines: list[str],
    chat_id: str | None = None,
) -> Notification:
    status = "stored"
    error: str | None = None
    sent_at: datetime | None = None

    destination, thread_id = notification_destination(target, chat_id)
    text = "\n".join(lines)

    if settings.telegram_bot_token and destination:
        try:
            payload_data: dict[str, object] = {
                "chat_id": destination,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if thread_id is not None:
                payload_data["message_thread_id"] = thread_id
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                    json=payload_data,
                )
                if response.is_error:
                    status = "failed"
                    error = f"Telegram API returned {response.status_code}: {response.text[:500]}"
                else:
                    status = "sent"
                    sent_at = datetime.now(timezone.utc)
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            status = "failed"
            error = f"{exc.__class__.__name__}: {exc}"

    notification = Notification(
        id=uuid4(),
        target=target,
        lines=lines,
        created_at=int(time.time() * 1000),
        delivery_status=status,  # type: ignore[arg-type]
        error=error,
    )

    async with async_session_factory() as session:
        session.add(
            NotificationORM(
                id=str(notification.id),
                target=target.value,
                chat_id=destination or None,
                thread_id=thread_id,
                text=text,
                parse_mode="HTML",
                delivery_status=status,
                error=error,
                sent_at=sent_at,
            )
        )
        await session.commit()

    return notification


async def list_notifications_async() -> list[Notification]:
    from sqlalchemy import select

    async with async_session_factory() as session:
        rows = (
            await session.execute(
                select(NotificationORM).order_by(NotificationORM.created_at.desc())
            )
        ).scalars()
        return [
            Notification(
                id=row.id,
                target=NotificationTarget(row.target),
                lines=row.text.splitlines(),
                created_at=int(row.created_at.timestamp() * 1000),
                delivery_status=row.delivery_status,  # type: ignore[arg-type]
                error=row.error,
            )
            for row in rows
        ]
