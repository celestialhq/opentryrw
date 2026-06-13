from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from uuid import uuid4

from .db import database
from .models import Notification, NotificationTarget
from .settings import settings


def send_telegram_notification(
    target: NotificationTarget,
    lines: list[str],
    chat_id: str | None = None,
) -> Notification:
    status = "stored"
    error: str | None = None

    destination, thread_id = notification_destination(target, chat_id)

    if settings.telegram_bot_token and destination:
        try:
            payload_data: dict[str, object] = {
                "chat_id": destination,
                "text": "\n".join(lines),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
            if thread_id is not None:
                payload_data["message_thread_id"] = thread_id
            payload = json.dumps(payload_data).encode()
            request = urllib.request.Request(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10):
                status = "sent"
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            status = "failed"
            error = str(exc)

    notification = Notification(
        id=uuid4(),
        target=target,
        lines=lines,
        created_at=int(time.time() * 1000),
        delivery_status=status,  # type: ignore[arg-type]
        error=error,
    )
    database.notifications.insert(notification.model_dump(mode="json"))
    return notification


def notification_destination(
    target: NotificationTarget,
    chat_id: str | None,
) -> tuple[str, int | None]:
    destination = chat_id or (
        settings.telegram_operator_chat_id if target == NotificationTarget.operator else ""
    )
    if target != NotificationTarget.operator or ":" not in destination:
        return destination, None

    group_id, thread_id = destination.rsplit(":", 1)
    try:
        return group_id, int(thread_id)
    except ValueError:
        return destination, None
