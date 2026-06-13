from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ComposeSource:
    stable_url: str
    dev_url: str


class Settings:
    app_name = "OpenTryRW"
    cookie_name = "opentryrw_session"
    session_ttl_seconds = 60 * 60
    deployment_ready_seconds = 27
    terminate_lock_seconds = 10 * 60
    session_cooldown_seconds = int(os.getenv("SESSION_COOLDOWN_SECONDS", str(24 * 60 * 60)))
    fingerprint_cooldown_seconds = int(
        os.getenv("FINGERPRINT_COOLDOWN_SECONDS", str(session_cooldown_seconds))
    )
    cleanup_interval_seconds = int(os.getenv("CLEANUP_INTERVAL_SECONDS", "30"))
    db_path = os.getenv("OPENTRYRW_DB", "data/opentryrw.json")
    database_url = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://opentryrw:opentryrw@postgres:5432/opentryrw",
    )
    database_echo = os.getenv("DATABASE_ECHO", "false").lower() == "true"
    storage_backend = os.getenv("STORAGE_BACKEND", "tinydb")
    cookie_secure = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    deployment_provider = os.getenv("DEPLOYMENT_PROVIDER", "mock")
    max_active_instances = os.getenv("MAX_ACTIVE_INSTANCES", "10").strip()
    digitalocean_token = os.getenv("DIGITALOCEAN_TOKEN", "")
    digitalocean_region = os.getenv("DIGITALOCEAN_REGION", "fra1")
    digitalocean_size = os.getenv("DIGITALOCEAN_SIZE", "s-1vcpu-2gb")
    digitalocean_image = os.getenv("DIGITALOCEAN_IMAGE", "ubuntu-24-04-x64")
    digitalocean_ssh_keys = [
        key.strip()
        for key in os.getenv("DIGITALOCEAN_SSH_KEYS", "").split(",")
        if key.strip()
    ]
    digitalocean_tag_prefix = os.getenv("DIGITALOCEAN_TAG_PREFIX", "opentryrw")
    deployment_public_domain_template = os.getenv(
        "DEPLOYMENT_PUBLIC_DOMAIN_TEMPLATE",
        "{ip}.sslip.io",
    )
    cloudflare_api_token = os.getenv("CLOUDFLARE_API_TOKEN", "")
    cloudflare_zone_id = os.getenv("CLOUDFLARE_ZONE_ID", "")
    cloudflare_root_domain = os.getenv("CLOUDFLARE_ROOT_DOMAIN", "").strip(".").lower()
    cloudflare_record_ttl = int(os.getenv("CLOUDFLARE_RECORD_TTL", "1"))
    compose = ComposeSource(
        stable_url=os.getenv(
            "REMNAWAVE_STABLE_COMPOSE_URL",
            "https://raw.githubusercontent.com/remnawave/backend/refs/heads/main/docker-compose-prod.yml",
        ),
        dev_url=os.getenv(
            "REMNAWAVE_DEV_COMPOSE_URL",
            "https://gist.githubusercontent.com/pius-pp/7ab391dc9659200cd22e1522d0b77582/raw/520f47cf546bd5802f298f23c399781ad6a55f4f/docker-compose-dev.yml",
        ),
    )
    remnawave_env_sample_url = os.getenv(
        "REMNAWAVE_ENV_SAMPLE_URL",
        "https://raw.githubusercontent.com/remnawave/backend/refs/heads/main/.env.sample",
    )
    deployment_health_timeout_seconds = int(os.getenv("DEPLOYMENT_HEALTH_TIMEOUT_SECONDS", "600"))
    provider_refresh_interval_seconds = int(os.getenv("PROVIDER_REFRESH_INTERVAL_SECONDS", "10"))
    reconciliation_interval_seconds = int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "300"))
    stale_resource_grace_seconds = int(os.getenv("STALE_RESOURCE_GRACE_SECONDS", "900"))
    worker_id = os.getenv("WORKER_ID", "opentryrw-worker")
    worker_poll_interval_seconds = int(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "5"))
    worker_batch_size = int(os.getenv("WORKER_BATCH_SIZE", "4"))
    worker_lock_seconds = int(os.getenv("WORKER_LOCK_SECONDS", "120"))
    abuse_hash_secret = (
        os.getenv("ABUSE_HASH_SECRET")
        or os.getenv("TELEGRAM_CLIENT_SECRET")
        or "opentryrw-local-dev"
    )
    trust_proxy_headers = os.getenv("TRUST_PROXY_HEADERS", "false").lower() == "true"
    rate_limit_auth_start_per_minute = int(os.getenv("RATE_LIMIT_AUTH_START_PER_MINUTE", "12"))
    rate_limit_auth_callback_per_minute = int(os.getenv("RATE_LIMIT_AUTH_CALLBACK_PER_MINUTE", "20"))
    rate_limit_session_create_per_hour = int(os.getenv("RATE_LIMIT_SESSION_CREATE_PER_HOUR", "6"))
    rate_limit_session_delete_per_minute = int(os.getenv("RATE_LIMIT_SESSION_DELETE_PER_MINUTE", "10"))
    telegram_client_id = os.getenv("TELEGRAM_CLIENT_ID", "")
    telegram_client_secret = os.getenv("TELEGRAM_CLIENT_SECRET", "")
    telegram_redirect_uri = os.getenv(
        "TELEGRAM_REDIRECT_URI",
        f"{os.getenv('FRONTEND_ORIGIN', 'http://127.0.0.1:4174')}/api/auth/telegram/callback",
    )
    telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_operator_chat_id = os.getenv("TELEGRAM_OPERATOR_CHAT_ID", "")
    frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://127.0.0.1:4174")

    @property
    def max_active_instances_value(self) -> int | None:
        raw = self.max_active_instances.lower()
        if raw in {"", "0", "inf", "infinity", "unlimited", "none"}:
            return None
        return max(1, int(raw))

    @property
    def cloudflare_enabled(self) -> bool:
        return bool(
            self.cloudflare_api_token
            and self.cloudflare_zone_id
            and self.cloudflare_root_domain
        )


settings = Settings()
