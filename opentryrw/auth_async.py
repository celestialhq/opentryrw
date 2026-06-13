from __future__ import annotations

import asyncio
import base64
import hashlib
import secrets
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import delete, select

from .auth import (
    AUTH_STATE_TTL_SECONDS,
    TELEGRAM_AUTH_URL,
    TelegramOAuthError,
    exchange_telegram_code,
    user_from_oidc_claims,
    verify_telegram_id_token,
)
from .database import async_session_factory
from .models import User
from .orm import AuthSessionORM, AuthStateORM, UserORM
from .settings import settings


def _now_ms() -> int:
    return int(time.time() * 1000)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _require_telegram_oauth_config() -> None:
    if not settings.telegram_client_id or not settings.telegram_client_secret:
        raise TelegramOAuthError("Telegram OAuth is not configured")


async def create_telegram_authorization_url_async() -> str:
    _require_telegram_oauth_config()
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _base64url(hashlib.sha256(code_verifier.encode()).digest())
    now = datetime.now(timezone.utc)

    async with async_session_factory() as session:
        session.add(
            AuthStateORM(
                state=state,
                code_verifier=code_verifier,
                created_at=now,
                expires_at=now + timedelta(seconds=AUTH_STATE_TTL_SECONDS),
            )
        )
        await session.commit()

    params = {
        "client_id": settings.telegram_client_id,
        "redirect_uri": settings.telegram_redirect_uri,
        "response_type": "code",
        "scope": "openid profile telegram:bot_access",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{TELEGRAM_AUTH_URL}?{urllib.parse.urlencode(params)}"


async def authenticate_telegram_code_async(code: str, state: str) -> User:
    code_verifier = await consume_auth_state_async(state)
    token_response = await asyncio.to_thread(exchange_telegram_code, code, code_verifier)
    id_token = token_response.get("id_token")
    if not isinstance(id_token, str):
        raise TelegramOAuthError("Telegram token response did not include id_token")
    claims = await asyncio.to_thread(verify_telegram_id_token, id_token)
    return user_from_oidc_claims(claims)


async def consume_auth_state_async(state: str) -> str:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        auth_state = await session.get(AuthStateORM, state)
        if not auth_state:
            raise TelegramOAuthError("Invalid Telegram auth state")

        await session.delete(auth_state)
        await session.commit()

        if auth_state.expires_at <= now:
            raise TelegramOAuthError("Expired Telegram auth state")

        return auth_state.code_verifier


async def persist_user_async(user: User) -> None:
    now = datetime.now(timezone.utc)
    async with async_session_factory() as session:
        existing = await session.get(UserORM, user.id)
        if existing:
            existing.telegram_username = user.username
            existing.display_name = user.name
            existing.avatar_initials = user.avatar_initials
            existing.last_login_at = now
        else:
            session.add(
                UserORM(
                    id=user.id,
                    telegram_id=user.id,
                    telegram_username=user.username,
                    display_name=user.name,
                    avatar_initials=user.avatar_initials,
                    trust_score=0,
                    is_blocked=False,
                    last_login_at=now,
                )
            )
        await session.commit()


async def create_auth_session_async(user: User) -> str:
    await persist_user_async(user)
    session_id = str(uuid4())
    async with async_session_factory() as session:
        session.add(
            AuthSessionORM(
                id=session_id,
                user_id=user.id,
                created_at=datetime.now(timezone.utc),
            )
        )
        await session.commit()
    return session_id


async def get_auth_user_async(session_id: str | None) -> User | None:
    if not session_id:
        return None

    async with async_session_factory() as session:
        auth_session = await session.get(AuthSessionORM, session_id)
        if not auth_session or auth_session.revoked_at is not None:
            return None
        user = await session.get(UserORM, auth_session.user_id)
        if not user or user.is_blocked:
            return None
        return User(
            id=user.id,
            name=user.display_name,
            username=user.telegram_username,
            avatar_initials=user.avatar_initials,
        )


async def delete_auth_session_async(session_id: str | None) -> None:
    if not session_id:
        return
    async with async_session_factory() as session:
        await session.execute(delete(AuthSessionORM).where(AuthSessionORM.id == session_id))
        await session.commit()
