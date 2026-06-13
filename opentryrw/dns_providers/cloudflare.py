from __future__ import annotations

import json
import random
import urllib.error
import urllib.request
from typing import Any

from opentryrw.settings import settings

from .base import DNSProvider, DNSProviderConfigError, DNSProviderError, DNSRecord

CLOUDFLARE_API = "https://api.cloudflare.com/client/v4"

MAMMALS = [
    "otter",
    "lynx",
    "marten",
    "badger",
    "beaver",
    "sable",
    "lemur",
    "panda",
    "koala",
    "bison",
    "tapir",
    "walrus",
    "ermine",
    "alpaca",
    "jaguar",
    "ocelot",
]

CAPITALS = [
    "oslo",
    "riga",
    "paris",
    "berlin",
    "vienna",
    "prague",
    "helsinki",
    "warsaw",
    "lisbon",
    "madrid",
    "rome",
    "tallinn",
    "vilnius",
    "dublin",
    "bern",
    "tokyo",
]


class CloudflareHTTPError(DNSProviderError):
    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        self.status_code = status_code
        message = f"Cloudflare API request failed: {method} {path} returned {status_code}"
        if body:
            message = f"{message} {body}"
        super().__init__(message)


class CloudflareDNSProvider(DNSProvider):
    name = "cloudflare"

    def reserve_host(self, deployment_id: str) -> str | None:
        if not settings.cloudflare_enabled:
            return None
        return generated_public_host(deployment_id)

    def publish(self, deployment: dict[str, Any], public_ip: str) -> DNSRecord:
        if not settings.cloudflare_enabled:
            raise DNSProviderConfigError("Cloudflare DNS is not configured")

        host = deployment.get("provider_public_host") or self.reserve_host(str(deployment["id"]))
        if not host:
            raise DNSProviderConfigError("Cloudflare host is not available")

        record_id = deployment.get("dns_record_id") or deployment.get("cloudflare_dns_record_id")
        if record_id:
            return DNSRecord(provider=self.name, host=str(host), record_id=str(record_id))

        response = cloudflare_request(
            "POST",
            f"/zones/{settings.cloudflare_zone_id}/dns_records",
            {
                "type": "A",
                "name": host,
                "content": public_ip,
                "ttl": settings.cloudflare_record_ttl,
                "proxied": True,
                "comment": f"{settings.app_name} temporary Remnawave instance",
            },
        )
        return DNSRecord(provider=self.name, host=str(host), record_id=str(response["result"]["id"]))

    def cleanup(self, deployment: dict[str, Any]) -> None:
        record_id = deployment.get("dns_record_id") or deployment.get("cloudflare_dns_record_id")
        if not record_id or not settings.cloudflare_enabled:
            return
        try:
            cloudflare_request(
                "DELETE",
                f"/zones/{settings.cloudflare_zone_id}/dns_records/{record_id}",
            )
        except CloudflareHTTPError as exc:
            if exc.status_code != 404:
                raise


def generated_public_host(deployment_id: str) -> str:
    root = settings.cloudflare_root_domain.strip(".").lower()
    if not root:
        raise DNSProviderConfigError("CLOUDFLARE_ROOT_DOMAIN is required for generated subdomains")
    if len(root) > 253:
        raise DNSProviderConfigError("CLOUDFLARE_ROOT_DOMAIN exceeds DNS length limits")

    label_limit = min(63, 255 - len(root) - 1)
    if label_limit < 1:
        raise DNSProviderConfigError("CLOUDFLARE_ROOT_DOMAIN leaves no room for a subdomain")

    seed = int(deployment_id.replace("-", "")[:16], 16)
    rng = random.Random(seed)
    short = deployment_id.replace("-", "")[:6]
    animal = rng.choice(MAMMALS)
    capital = rng.choice(CAPITALS)
    label = fit_dns_label(f"{animal}-{capital}-{short}", animal, capital, short, label_limit)
    fqdn = f"{label}.{root}"
    if len(label) > 63 or len(fqdn) > 255:
        raise DNSProviderConfigError("Generated deployment domain exceeds DNS limits")
    return fqdn


def fit_dns_label(candidate: str, animal: str, capital: str, short: str, limit: int) -> str:
    if len(candidate) <= limit:
        return candidate

    suffix = f"-{short}"
    capital_budget = limit - len(animal) - len(suffix) - 1
    if capital_budget >= 1:
        return f"{animal}-{capital[:capital_budget]}{suffix}"

    animal_budget = limit - len(suffix)
    if animal_budget >= 1:
        return f"{animal[:animal_budget]}{suffix}"

    return short[:limit]


def cloudflare_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{CLOUDFLARE_API}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {settings.cloudflare_api_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status == 204:
                return {}
            body = json.loads(response.read().decode())
            if body.get("success") is False:
                raise CloudflareHTTPError(method, path, response.status, json.dumps(body))
            return body
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode()
        except OSError:
            body = ""
        raise CloudflareHTTPError(method, path, exc.code, body) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise DNSProviderError(f"Cloudflare API request failed: {method} {path}") from exc
