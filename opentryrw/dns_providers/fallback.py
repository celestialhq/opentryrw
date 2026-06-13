from __future__ import annotations

from typing import Any

from opentryrw.settings import settings

from .base import DNSProvider, DNSRecord


class FallbackDNSProvider(DNSProvider):
    name = "fallback"

    def publish(self, deployment: dict[str, Any], public_ip: str) -> DNSRecord:
        del deployment
        return DNSRecord(provider=self.name, host=sslip_host(public_ip))


def sslip_host(public_ip: str) -> str:
    return settings.deployment_public_domain_template.format(ip=public_ip)
