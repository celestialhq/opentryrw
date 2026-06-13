from __future__ import annotations

from urllib.parse import urlparse

from .models import DeploymentProvider
from .settings import settings


def readiness_report() -> dict:
    checks: list[dict[str, str]] = []

    def add(name: str, status: str, message: str) -> None:
        checks.append({"name": name, "status": status, "message": message})

    add(
        "storage_backend",
        "pass" if settings.storage_backend == "postgres" else "fail",
        "Production launches should use STORAGE_BACKEND=postgres.",
    )
    add(
        "database_url",
        "fail" if "opentryrw:opentryrw" in settings.database_url else "pass",
        "DATABASE_URL should not use default development credentials.",
    )
    add(
        "cookie_secure",
        "pass" if settings.cookie_secure else "fail",
        "Set COOKIE_SECURE=true behind HTTPS before beta launch.",
    )
    add(
        "frontend_origin",
        "pass" if is_public_https_origin(settings.frontend_origin) else "warn",
        "FRONTEND_ORIGIN should be the public HTTPS origin, not localhost.",
    )
    add(
        "abuse_hash_secret",
        "pass" if strong_secret(settings.abuse_hash_secret) else "fail",
        "ABUSE_HASH_SECRET should be set to a random value with at least 32 characters.",
    )
    add(
        "telegram_auth",
        "pass" if settings.telegram_client_id and settings.telegram_client_secret else "fail",
        "Telegram OAuth credentials are required.",
    )
    add(
        "telegram_redirect_uri",
        "pass" if settings.telegram_redirect_uri.startswith("https://") else "warn",
        "TELEGRAM_REDIRECT_URI should use HTTPS in beta/prod.",
    )
    add(
        "telegram_notifications",
        "pass" if settings.telegram_bot_token and settings.telegram_operator_chat_id else "fail",
        "Telegram bot token and operator chat are required for operational visibility.",
    )
    add_provider_checks(add)
    add(
        "capacity",
        "pass" if beta_capacity_is_conservative() else "warn",
        "Use a low MAX_ACTIVE_INSTANCES value for beta launch.",
    )
    add(
        "cooldown",
        "pass" if settings.session_cooldown_seconds >= 60 * 60 else "warn",
        "Keep session cooldown at least one hour for beta launch.",
    )

    failures = [check for check in checks if check["status"] == "fail"]
    warnings = [check for check in checks if check["status"] == "warn"]
    return {
        "ready": not failures,
        "checks": checks,
        "failures": len(failures),
        "warnings": len(warnings),
    }


def add_provider_checks(add) -> None:
    try:
        provider = DeploymentProvider(settings.deployment_provider)
    except ValueError:
        add(
            "deployment_provider",
            "fail",
            f"Unsupported DEPLOYMENT_PROVIDER={settings.deployment_provider}.",
        )
        return

    if provider == DeploymentProvider.mock:
        add(
            "deployment_provider",
            "warn",
            "DEPLOYMENT_PROVIDER=mock is fine for smoke tests, but beta needs a real provider.",
        )
        return

    if provider == DeploymentProvider.digitalocean:
        add(
            "digitalocean_token",
            "pass" if settings.digitalocean_token else "fail",
            "DIGITALOCEAN_TOKEN is required for DigitalOcean beta launch.",
        )
        add(
            "digitalocean_size",
            "pass" if settings.digitalocean_size else "fail",
            "DIGITALOCEAN_SIZE must be set.",
        )

    if settings.cloudflare_enabled:
        add(
            "cloudflare_dns",
            "pass",
            "Cloudflare proxied DNS is configured.",
        )
    else:
        add(
            "cloudflare_dns",
            "warn",
            "Cloudflare DNS is not fully configured; sslip.io fallback will be used.",
        )


def is_public_https_origin(origin: str) -> bool:
    parsed = urlparse(origin)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host not in {"localhost", "127.0.0.1", "::1"}


def strong_secret(value: str) -> bool:
    return bool(value and value != "opentryrw-local-dev" and len(value) >= 32)


def beta_capacity_is_conservative() -> bool:
    limit = settings.max_active_instances_value
    return limit is not None and 1 <= limit <= 3
