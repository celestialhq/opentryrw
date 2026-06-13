from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4

import jwt

from .db import Q, database
from .models import User
from .settings import settings

TELEGRAM_AUTH_URL = "https://oauth.telegram.org/auth"
TELEGRAM_TOKEN_URL = "https://oauth.telegram.org/token"
TELEGRAM_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
TELEGRAM_ISSUER = "https://oauth.telegram.org"
AUTH_STATE_TTL_SECONDS = 10 * 60


class TelegramOAuthError(Exception):
    pass


def _now_ms() -> int:
    return int(time.time() * 1000)


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _require_telegram_oauth_config() -> None:
    if not settings.telegram_client_id or not settings.telegram_client_secret:
        raise TelegramOAuthError("Telegram OAuth is not configured")


def create_telegram_authorization_url() -> str:
    _require_telegram_oauth_config()
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = _base64url(hashlib.sha256(code_verifier.encode()).digest())

    database.auth_states.insert(
        {
            "state": state,
            "code_verifier": code_verifier,
            "created_at": _now_ms(),
        }
    )

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


def authenticate_telegram_code(code: str, state: str) -> User:
    code_verifier = consume_auth_state(state)
    token_response = exchange_telegram_code(code, code_verifier)
    id_token = token_response.get("id_token")
    if not isinstance(id_token, str):
        raise TelegramOAuthError("Telegram token response did not include id_token")
    claims = verify_telegram_id_token(id_token)
    return user_from_oidc_claims(claims)


def consume_auth_state(state: str) -> str:
    auth_state = database.auth_states.get(Q.state == state)
    database.auth_states.remove(Q.state == state)
    if not auth_state:
        raise TelegramOAuthError("Invalid Telegram auth state")

    age_seconds = (_now_ms() - int(auth_state["created_at"])) / 1000
    if age_seconds > AUTH_STATE_TTL_SECONDS:
        raise TelegramOAuthError("Expired Telegram auth state")

    return str(auth_state["code_verifier"])


def exchange_telegram_code(code: str, code_verifier: str) -> dict:
    _require_telegram_oauth_config()
    body = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": settings.telegram_redirect_uri,
            "client_id": settings.telegram_client_id,
            "code_verifier": code_verifier,
        }
    ).encode()
    basic = base64.b64encode(
        f"{settings.telegram_client_id}:{settings.telegram_client_secret}".encode()
    ).decode()
    request = urllib.request.Request(
        TELEGRAM_TOKEN_URL,
        data=body,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise TelegramOAuthError("Telegram token exchange failed") from exc


def verify_telegram_id_token(id_token: str) -> dict:
    _require_telegram_oauth_config()
    try:
        jwks_client = jwt.PyJWKClient(TELEGRAM_JWKS_URL)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.telegram_client_id,
            issuer=TELEGRAM_ISSUER,
        )
    except jwt.PyJWTError as exc:
        raise TelegramOAuthError("Invalid Telegram id_token") from exc
    if not isinstance(claims, dict):
        raise TelegramOAuthError("Invalid Telegram claims")
    return claims


def user_from_oidc_claims(claims: dict) -> User:
    telegram_id = claims.get("id") or claims.get("sub")
    if not telegram_id:
        raise TelegramOAuthError("Telegram id_token has no user id")

    name = str(claims.get("name") or "Telegram User")
    username = claims.get("preferred_username")
    return User(
        id=str(telegram_id),
        name=name,
        username=str(username) if username else None,
        avatar_initials=name[:1].upper() if name else "T",
    )


def persist_user(user: User) -> None:
    existing = database.users.get(Q.id == user.id)
    document = user.model_dump()
    if existing:
        database.users.update(document, Q.id == user.id)
    else:
        database.users.insert(document)


def create_auth_session(user: User) -> str:
    persist_user(user)
    session_id = str(uuid4())
    database.auth_sessions.insert(
        {
            "id": session_id,
            "user_id": user.id,
            "created_at": _now_ms(),
        }
    )
    return session_id


def get_auth_user(session_id: str | None) -> User | None:
    if not session_id:
        return None

    auth_session = database.auth_sessions.get(Q.id == session_id)
    if not auth_session:
        return None

    user = database.users.get(Q.id == auth_session["user_id"])
    return User.model_validate(user) if user else None


def delete_auth_session(session_id: str | None) -> None:
    if not session_id:
        return
    database.auth_sessions.remove(Q.id == session_id)
