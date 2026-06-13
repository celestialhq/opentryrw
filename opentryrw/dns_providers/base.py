from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


class DNSProviderError(Exception):
    pass


class DNSProviderConfigError(DNSProviderError):
    pass


@dataclass(frozen=True)
class DNSRecord:
    provider: str
    host: str
    record_id: str | None = None
    error: str | None = None

    @property
    def url(self) -> str:
        return f"https://{self.host}"


class DNSProvider(ABC):
    name: str

    def reserve_host(self, deployment_id: str) -> str | None:
        del deployment_id
        return None

    @abstractmethod
    def publish(self, deployment: dict[str, Any], public_ip: str) -> DNSRecord:
        raise NotImplementedError

    def cleanup(self, deployment: dict[str, Any]) -> None:
        del deployment
