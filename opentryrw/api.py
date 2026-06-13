from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from collections.abc import AsyncIterator

from fastapi import Cookie, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select, text

from .auth import (
    TelegramOAuthError,
    authenticate_telegram_code,
    create_auth_session,
    create_telegram_authorization_url,
    delete_auth_session,
    get_auth_user,
)
from .auth_async import (
    authenticate_telegram_code_async,
    create_auth_session_async,
    create_telegram_authorization_url_async,
    delete_auth_session_async,
    get_auth_user_async,
)
from .database import close_database
from .database import async_session_factory
from .db import database
from .deployments import (
    CapacityExceededError,
    CooldownActiveError,
    TerminateLockedError,
    cleanup_expired_sessions,
    create_session,
    get_cooldown_until,
    get_session,
    terminate_session,
)
from .deployments_async import (
    cleanup_expired_sessions_async,
    create_session_async,
    get_cooldown_until_async,
    get_session_async,
    terminate_session_async,
)
from .models import (
    AuthResponse,
    AuthStatusResponse,
    CreateSessionRequest,
    Notification,
    NotificationsResponse,
    SessionResponse,
    User,
)
from .orm import DeploymentJobORM, DeploymentORM, ProviderResourceORM
from .providers import ProviderConfigError, ProviderError
from .notifications_async import list_notifications_async
from .readiness import readiness_report
from .security import (
    RateLimitExceededError,
    client_ip,
    enforce_rate_limit_async,
    get_fingerprint_cooldown_until_async,
    request_fingerprint,
)
from .settings import settings

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_DIST = ROOT / "frontend" / "dist"


async def cleanup_loop(stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            if settings.storage_backend == "postgres":
                await cleanup_expired_sessions_async()
            else:
                await asyncio.to_thread(cleanup_expired_sessions)
        except Exception:
            # This loop is a temporary bridge until cleanup moves into the worker.
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.cleanup_interval_seconds)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app_instance: FastAPI) -> AsyncIterator[None]:
    del app_instance
    stop_event = asyncio.Event()
    cleanup_task = asyncio.create_task(cleanup_loop(stop_event), name="opentryrw-cleanup")
    try:
        yield
    finally:
        stop_event.set()
        await cleanup_task
        await close_database()


app = FastAPI(
    title="OpenTryRW API",
    version="0.1.0",
    description="API for temporary Remnawave demo instance provisioning.",
    lifespan=lifespan,
)


def set_auth_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        settings.cookie_name,
        session_id,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=24 * 60 * 60,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/")


def rate_limit_exception(exc: RateLimitExceededError) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Rate limit exceeded",
        headers={"Retry-After": str(exc.retry_after)},
    )


def merge_cooldowns(*values: int | None) -> int | None:
    active = [value for value in values if value is not None]
    return max(active) if active else None


async def require_auth(opentryrw_session: str | None = Cookie(default=None)) -> tuple[str, User]:
    if settings.storage_backend == "postgres":
        user = await get_auth_user_async(opentryrw_session)
    else:
        user = await asyncio.to_thread(get_auth_user, opentryrw_session)
    if not user or not opentryrw_session:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return opentryrw_session, user


@app.get("/api/health", tags=["ops"])
async def health() -> dict:
    db_status = "disabled"
    if settings.storage_backend == "postgres":
        try:
            async with async_session_factory() as session:
                await session.execute(text("select 1"))
            db_status = "ok"
        except Exception:
            db_status = "error"
    return {
        "status": "ok" if db_status != "error" else "error",
        "storage_backend": settings.storage_backend,
        "database": db_status,
        "deployment_provider": settings.deployment_provider,
    }


@app.get("/api/metrics", tags=["ops"])
async def metrics() -> Response:
    lines = [
        "# HELP opentryrw_info Static service info.",
        "# TYPE opentryrw_info gauge",
        f'opentryrw_info{{storage_backend="{settings.storage_backend}",deployment_provider="{settings.deployment_provider}"}} 1',
    ]
    if settings.storage_backend == "postgres":
        async with async_session_factory() as session:
            deployment_counts = (
                await session.execute(
                    select(DeploymentORM.status, func.count())
                    .group_by(DeploymentORM.status)
                    .order_by(DeploymentORM.status)
                )
            ).all()
            job_counts = (
                await session.execute(
                    select(DeploymentJobORM.status, func.count())
                    .group_by(DeploymentJobORM.status)
                    .order_by(DeploymentJobORM.status)
                )
            ).all()
            active_resources = await session.scalar(
                select(func.count()).select_from(ProviderResourceORM).where(
                    ProviderResourceORM.deleted_at.is_(None)
                )
            )
        lines.extend(
            [
                "# HELP opentryrw_deployments_total Deployments by status.",
                "# TYPE opentryrw_deployments_total gauge",
            ]
        )
        for status, count in deployment_counts:
            lines.append(f'opentryrw_deployments_total{{status="{status}"}} {int(count)}')
        lines.extend(
            [
                "# HELP opentryrw_deployment_jobs_total Deployment jobs by status.",
                "# TYPE opentryrw_deployment_jobs_total gauge",
            ]
        )
        for status, count in job_counts:
            lines.append(f'opentryrw_deployment_jobs_total{{status="{status}"}} {int(count)}')
        lines.extend(
            [
                "# HELP opentryrw_provider_resources_active Active tracked provider resources.",
                "# TYPE opentryrw_provider_resources_active gauge",
                f"opentryrw_provider_resources_active {int(active_resources or 0)}",
            ]
        )
    return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@app.get("/api/readiness", tags=["ops"])
async def readiness() -> dict:
    return readiness_report()


@app.get("/api/auth/status", response_model=AuthStatusResponse, tags=["auth"])
async def auth_status(opentryrw_session: str | None = Cookie(default=None)) -> AuthStatusResponse:
    if settings.storage_backend == "postgres":
        user = await get_auth_user_async(opentryrw_session)
    else:
        user = await asyncio.to_thread(get_auth_user, opentryrw_session)
    return AuthStatusResponse(
        authenticated=user is not None,
        user=user,
        telegram_auth_enabled=bool(settings.telegram_client_id and settings.telegram_client_secret),
    )


@app.get("/api/auth/telegram/start", tags=["auth"])
async def telegram_start(request: Request) -> RedirectResponse:
    try:
        await enforce_rate_limit_async(
            "auth_start_ip",
            client_ip(request),
            limit=settings.rate_limit_auth_start_per_minute,
            window_seconds=60,
        )
        if settings.storage_backend == "postgres":
            auth_url = await create_telegram_authorization_url_async()
        else:
            auth_url = await asyncio.to_thread(create_telegram_authorization_url)
    except RateLimitExceededError as exc:
        raise rate_limit_exception(exc) from exc
    except TelegramOAuthError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return RedirectResponse(auth_url, status_code=303)


@app.get("/api/auth/telegram/callback", tags=["auth"])
async def telegram_callback(request: Request, code: str, state: str) -> RedirectResponse:
    try:
        await enforce_rate_limit_async(
            "auth_callback_ip",
            client_ip(request),
            limit=settings.rate_limit_auth_callback_per_minute,
            window_seconds=60,
        )
        if settings.storage_backend == "postgres":
            user = await authenticate_telegram_code_async(code, state)
        else:
            user = await asyncio.to_thread(authenticate_telegram_code, code, state)
    except RateLimitExceededError as exc:
        raise rate_limit_exception(exc) from exc
    except TelegramOAuthError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if settings.storage_backend == "postgres":
        session_id = await create_auth_session_async(user)
    else:
        session_id = await asyncio.to_thread(create_auth_session, user)
    response = RedirectResponse("/console", status_code=303)
    set_auth_cookie(response, session_id)
    return response


@app.post("/api/auth/logout", response_model=AuthResponse, tags=["auth"])
async def logout(
    response: Response,
    opentryrw_session: str | None = Cookie(default=None),
) -> AuthResponse:
    if settings.storage_backend == "postgres":
        await delete_auth_session_async(opentryrw_session)
    else:
        await asyncio.to_thread(delete_auth_session, opentryrw_session)
    clear_auth_cookie(response)
    return AuthResponse(authenticated=False)


@app.get("/api/session", response_model=SessionResponse, tags=["sessions"])
async def active_session(
    request: Request,
    auth: tuple[str, User] = Depends(require_auth),
) -> SessionResponse:
    auth_session_id, user = auth
    if settings.storage_backend == "postgres":
        fingerprint_hash = request_fingerprint(request)
        session, user_cooldown_until, fingerprint_cooldown_until = await asyncio.gather(
            get_session_async(auth_session_id, user),
            get_cooldown_until_async(user),
            get_fingerprint_cooldown_until_async(fingerprint_hash),
        )
        cooldown_until = merge_cooldowns(user_cooldown_until, fingerprint_cooldown_until)
    else:
        session, cooldown_until = await asyncio.gather(
            asyncio.to_thread(get_session, auth_session_id, user),
            asyncio.to_thread(get_cooldown_until, user),
        )
    return SessionResponse(
        session=session,
        cooldown_until=cooldown_until,
        can_create_session=session is None and cooldown_until is None,
    )


@app.post("/api/session", response_model=SessionResponse, status_code=201, tags=["sessions"])
async def request_session(
    request: Request,
    payload: CreateSessionRequest,
    auth: tuple[str, User] = Depends(require_auth),
) -> SessionResponse:
    auth_session_id, user = auth
    fingerprint_hash = request_fingerprint(request)
    try:
        await enforce_rate_limit_async(
            "session_create_user",
            user.id,
            limit=settings.rate_limit_session_create_per_hour,
            window_seconds=60 * 60,
        )
        await enforce_rate_limit_async(
            "session_create_fingerprint",
            fingerprint_hash,
            limit=settings.rate_limit_session_create_per_hour,
            window_seconds=60 * 60,
        )
        if settings.storage_backend == "postgres":
            session = await create_session_async(
                auth_session_id,
                user,
                payload,
                fingerprint_hash=fingerprint_hash,
            )
        else:
            session = await asyncio.to_thread(create_session, auth_session_id, user, payload)
    except RateLimitExceededError as exc:
        raise rate_limit_exception(exc) from exc
    except CooldownActiveError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Session cooldown is active",
                "cooldown_until": exc.cooldown_until,
            },
        ) from exc
    except CapacityExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail={
                "message": "Service capacity is full",
                "active": exc.active,
                "limit": exc.limit,
            },
        ) from exc
    except ProviderConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if settings.storage_backend == "postgres":
        cooldown_until = merge_cooldowns(
            await get_cooldown_until_async(user),
            await get_fingerprint_cooldown_until_async(fingerprint_hash),
        )
    else:
        cooldown_until = await asyncio.to_thread(get_cooldown_until, user)
    return SessionResponse(
        session=session,
        cooldown_until=cooldown_until,
        can_create_session=False,
    )


@app.delete("/api/session", response_model=SessionResponse, tags=["sessions"])
async def delete_session(
    request: Request,
    auth: tuple[str, User] = Depends(require_auth),
) -> SessionResponse:
    auth_session_id, user = auth
    fingerprint_hash = request_fingerprint(request)
    try:
        await enforce_rate_limit_async(
            "session_delete_fingerprint",
            fingerprint_hash,
            limit=settings.rate_limit_session_delete_per_minute,
            window_seconds=60,
        )
        if settings.storage_backend == "postgres":
            await terminate_session_async(auth_session_id, user)
        else:
            await asyncio.to_thread(terminate_session, auth_session_id, user)
    except RateLimitExceededError as exc:
        raise rate_limit_exception(exc) from exc
    except TerminateLockedError as exc:
        raise HTTPException(status_code=409, detail="Terminate is locked") from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if settings.storage_backend == "postgres":
        cooldown_until = merge_cooldowns(
            await get_cooldown_until_async(user),
            await get_fingerprint_cooldown_until_async(fingerprint_hash),
        )
    else:
        cooldown_until = await asyncio.to_thread(get_cooldown_until, user)
    return SessionResponse(
        session=None,
        cooldown_until=cooldown_until,
        can_create_session=cooldown_until is None,
    )


@app.get("/api/notifications", response_model=NotificationsResponse, tags=["notifications"])
async def list_notifications(auth: tuple[str, User] = Depends(require_auth)) -> NotificationsResponse:
    del auth
    if settings.storage_backend == "postgres":
        return NotificationsResponse(notifications=await list_notifications_async())
    notifications = await asyncio.to_thread(database.notifications.all)
    return NotificationsResponse(
        notifications=[Notification.model_validate(item) for item in notifications]
    )


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(frontend_index_path())


@app.get("/{file_path:path}", include_in_schema=False)
async def static_file(file_path: str) -> FileResponse:
    if file_path.startswith("api/"):
        raise HTTPException(status_code=404)

    static_root = frontend_static_root()
    path = (static_root / file_path).resolve()
    if str(path).startswith(str(static_root.resolve())) and path.is_file():
        return FileResponse(path)

    return FileResponse(frontend_index_path())


def frontend_static_root() -> Path:
    return FRONTEND_DIST


def frontend_index_path() -> Path:
    index_path = frontend_static_root() / "index.html"
    if not index_path.is_file():
        raise HTTPException(status_code=503, detail="Frontend build is missing")
    return index_path
