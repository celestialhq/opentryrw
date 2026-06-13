from __future__ import annotations

import hmac
import time
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.requests import Request

from .database import async_session_factory
from .orm import AbuseSignalORM, RateLimitORM
from .settings import settings

_MEMORY_RATE_LIMITS: dict[str, tuple[int, int]] = {}


class RateLimitExceededError(Exception):
    def __init__(self, retry_after: int) -> None:
        self.retry_after = max(1, retry_after)
        super().__init__("Rate limit exceeded")


def client_ip(request: Request) -> str:
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip() or "unknown"
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip() or "unknown"
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def stable_hash(value: str) -> str:
    return hmac.new(
        settings.abuse_hash_secret.encode(),
        value.encode(),
        sha256,
    ).hexdigest()


def request_fingerprint(request: Request) -> str:
    user_agent = request.headers.get("user-agent", "")
    accept_language = request.headers.get("accept-language", "")
    return stable_hash(f"{client_ip(request)}\n{user_agent}\n{accept_language}")


async def enforce_rate_limit_async(
    scope: str,
    identifier: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    if limit <= 0 or window_seconds <= 0:
        return
    if settings.storage_backend != "postgres":
        enforce_memory_rate_limit(scope, identifier, limit=limit, window_seconds=window_seconds)
        return

    key = f"{scope}:{stable_hash(identifier)[:96]}"
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=window_seconds)

    try:
        async with async_session_factory() as session:
            async with session.begin():
                counter = (
                    await session.execute(
                        select(RateLimitORM)
                        .where(RateLimitORM.key == key)
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if not counter:
                    session.add(
                        RateLimitORM(
                            key=key,
                            window_start=now,
                            count=1,
                            expires_at=expires_at,
                        )
                    )
                    return
                if counter.expires_at <= now:
                    counter.window_start = now
                    counter.count = 1
                    counter.expires_at = expires_at
                    return
                if counter.count >= limit:
                    retry_after = int((counter.expires_at - now).total_seconds())
                    raise RateLimitExceededError(retry_after)
                counter.count += 1
    except IntegrityError:
        await enforce_rate_limit_async(
            scope,
            identifier,
            limit=limit,
            window_seconds=window_seconds,
        )


def enforce_memory_rate_limit(
    scope: str,
    identifier: str,
    *,
    limit: int,
    window_seconds: int,
) -> None:
    now = int(time.time())
    key = f"{scope}:{stable_hash(identifier)[:96]}"
    count, expires_at = _MEMORY_RATE_LIMITS.get(key, (0, now + window_seconds))
    if expires_at <= now:
        count = 0
        expires_at = now + window_seconds
    if count >= limit:
        raise RateLimitExceededError(expires_at - now)
    _MEMORY_RATE_LIMITS[key] = (count + 1, expires_at)


async def get_fingerprint_cooldown_until_async(fingerprint_hash: str) -> int | None:
    if settings.storage_backend != "postgres" or settings.fingerprint_cooldown_seconds <= 0:
        return None

    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        signal = (
            await session.execute(
                select(AbuseSignalORM)
                .where(
                    AbuseSignalORM.signal_type == "fingerprint_cooldown",
                    AbuseSignalORM.value_hash == fingerprint_hash,
                    AbuseSignalORM.expires_at > now,
                )
                .order_by(AbuseSignalORM.expires_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if not signal or not signal.expires_at:
            return None
        return int(signal.expires_at.timestamp() * 1000)


async def set_fingerprint_cooldown_async(
    user_id: str,
    fingerprint_hash: str,
    now: datetime,
) -> int | None:
    if settings.storage_backend != "postgres" or settings.fingerprint_cooldown_seconds <= 0:
        return None

    expires_at = now + timedelta(seconds=settings.fingerprint_cooldown_seconds)
    async with async_session_factory() as session:
        signal = (
            await session.execute(
                select(AbuseSignalORM)
                .where(
                    AbuseSignalORM.signal_type == "fingerprint_cooldown",
                    AbuseSignalORM.value_hash == fingerprint_hash,
                    AbuseSignalORM.expires_at > now,
                )
                .order_by(AbuseSignalORM.expires_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if signal:
            signal.user_id = user_id
            signal.expires_at = expires_at
            signal.weight = max(signal.weight, 1)
        else:
            session.add(
                AbuseSignalORM(
                    user_id=user_id,
                    signal_type="fingerprint_cooldown",
                    value_hash=fingerprint_hash,
                    weight=1,
                    expires_at=expires_at,
                )
            )
        await session.commit()
    return int(expires_at.timestamp() * 1000)
