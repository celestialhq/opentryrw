from __future__ import annotations

import ssl
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from opentryrw.dns_providers import (
    CloudflareDNSProvider,
    DNSProviderError,
    DNSRecord,
    FallbackDNSProvider,
)
from opentryrw.models import CreateSessionRequest, DeploymentProvider, DeploymentStatus
from opentryrw.settings import settings

from .base import DeploymentAdapter, ProviderConfigError, ProviderError, ProviderRefresh
from .digitalocean import DigitalOceanProvider
from .mock import MockProvider

PROVIDERS: dict[DeploymentProvider, DeploymentAdapter] = {
    DeploymentProvider.mock: MockProvider(),
    DeploymentProvider.digitalocean: DigitalOceanProvider(),
}

PRIMARY_DNS = CloudflareDNSProvider()
FALLBACK_DNS = FallbackDNSProvider()

HEALTH_CHECK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
}


def selected_provider(request: CreateSessionRequest | None = None) -> DeploymentProvider:
    del request
    try:
        return DeploymentProvider(settings.deployment_provider)
    except ValueError as exc:
        raise ProviderConfigError(
            f"Unsupported DEPLOYMENT_PROVIDER: {settings.deployment_provider}"
        ) from exc


def provider_adapter(provider: DeploymentProvider) -> DeploymentAdapter:
    try:
        return PROVIDERS[provider]
    except KeyError as exc:
        raise ProviderConfigError(f"Unsupported deployment provider: {provider}") from exc


def provision_provider(
    deployment_id: str,
    user_id: str,
    request: CreateSessionRequest,
) -> dict[str, Any]:
    public_host = PRIMARY_DNS.reserve_host(deployment_id)
    provider_data = provider_adapter(selected_provider(request)).provision(
        deployment_id,
        user_id,
        request,
        public_host,
    )
    if public_host:
        provider_data.setdefault("provider_public_host", public_host)
        provider_data.setdefault("url", f"https://{public_host}")
        provider_data.setdefault("dns_provider", PRIMARY_DNS.name)
        provider_data.setdefault("dns_record_id", None)
    return provider_data


def refresh_provider(deployment: dict[str, Any]) -> ProviderRefresh:
    provider = DeploymentProvider(deployment.get("provider", DeploymentProvider.mock.value))
    refresh = provider_adapter(provider).refresh(deployment)
    if not refresh.provider_patch:
        return refresh

    provider_patch = dict(refresh.provider_patch)
    public_ip = provider_patch.get("provider_public_ip")
    url = refresh.url or deployment.get("url")
    status = refresh.status
    progress = refresh.progress_percent

    if public_ip:
        dns_record = ensure_public_endpoint(deployment, str(public_ip))
        provider_patch["dns_provider"] = dns_record.provider
        provider_patch["dns_record_id"] = dns_record.record_id
        provider_patch["provider_public_host"] = dns_record.host
        provider_patch["cloudflare_dns_record_id"] = (
            dns_record.record_id if dns_record.provider == "cloudflare" else None
        )
        if dns_record.error:
            provider_patch["dns_error"] = dns_record.error
            provider_patch["cloudflare_error"] = dns_record.error
        url = dns_record.url

    if status == DeploymentStatus.deploying and not deployment.get("health_started_at"):
        provider_patch["health_started_at"] = datetime.now(timezone.utc)

    if status not in {DeploymentStatus.ready, DeploymentStatus.terminated}:
        if url and health_check(url):
            status = DeploymentStatus.ready
            progress = 100
        elif deployment_health_timed_out(deployment):
            provider_patch["error_message"] = (
                f"{provider.value} deployment did not return HTTP 200 before the health timeout"
            )
            status = DeploymentStatus.failed
            progress = 95

    return ProviderRefresh(
        status=status,
        url=url,
        progress_percent=progress,
        provider_patch=provider_patch,
    )


def destroy_provider(deployment: dict[str, Any]) -> None:
    provider = DeploymentProvider(deployment.get("provider", DeploymentProvider.mock.value))
    errors: list[Exception] = []
    try:
        provider_adapter(provider).destroy(deployment)
    except ProviderError as exc:
        errors.append(exc)

    try:
        cleanup_public_endpoint(deployment)
    except DNSProviderError as exc:
        errors.append(exc)

    if errors:
        raise ProviderError("; ".join(str(error) for error in errors))


def ensure_public_endpoint(deployment: dict[str, Any], public_ip: str) -> DNSRecord:
    record_id = deployment.get("dns_record_id") or deployment.get("cloudflare_dns_record_id")
    dns_provider = deployment.get("dns_provider")
    if dns_provider == PRIMARY_DNS.name and record_id and deployment.get("provider_public_host"):
        return DNSRecord(
            provider=PRIMARY_DNS.name,
            host=str(deployment["provider_public_host"]),
            record_id=str(record_id),
        )

    if settings.cloudflare_enabled:
        try:
            return PRIMARY_DNS.publish(deployment, public_ip)
        except DNSProviderError as exc:
            fallback_record = FALLBACK_DNS.publish(deployment, public_ip)
            return DNSRecord(
                provider=fallback_record.provider,
                host=fallback_record.host,
                error=str(exc),
            )

    return FALLBACK_DNS.publish(deployment, public_ip)


def cleanup_public_endpoint(deployment: dict[str, Any]) -> None:
    dns_provider = deployment.get("dns_provider")
    if dns_provider == PRIMARY_DNS.name or deployment.get("cloudflare_dns_record_id"):
        PRIMARY_DNS.cleanup(deployment)


def deployment_health_timed_out(deployment: dict[str, Any]) -> bool:
    health_started_at = deployment.get("health_started_at")
    if not health_started_at:
        return False
    elapsed_seconds = max(0, (_now_ms() - int(health_started_at)) / 1000)
    return elapsed_seconds > settings.deployment_health_timeout_seconds


def health_check(url: str) -> bool:
    context = ssl.create_default_context()
    request = urllib.request.Request(url, headers=HEALTH_CHECK_HEADERS, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=8, context=context) as response:
            body = response.read(65_536).decode(errors="ignore").lower()
            return response.status == 200 and is_acceptable_ready_response(body)
    except urllib.error.HTTPError as exc:
        return False
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def is_acceptable_ready_response(body: str) -> bool:
    body = body.lower()
    if not body:
        return False
    blocked_markers = (
        "web server is down",
        "error code 521",
        "cloudflare ray id",
        "caddy",
        "congratulations",
        "reverse_proxy",
    )
    if any(marker in body for marker in blocked_markers):
        return False
    return True


def _now_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "DeploymentAdapter",
    "ProviderConfigError",
    "ProviderError",
    "ProviderRefresh",
    "destroy_provider",
    "provision_provider",
    "refresh_provider",
    "selected_provider",
]
