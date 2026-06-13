from __future__ import annotations

from .base import DNSProvider, DNSProviderConfigError, DNSProviderError, DNSRecord
from .cloudflare import CloudflareDNSProvider, generated_public_host
from .fallback import FallbackDNSProvider, sslip_host

__all__ = [
    "CloudflareDNSProvider",
    "DNSProvider",
    "DNSProviderConfigError",
    "DNSProviderError",
    "DNSRecord",
    "FallbackDNSProvider",
    "generated_public_host",
    "sslip_host",
]
