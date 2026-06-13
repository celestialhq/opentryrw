from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from opentryrw.models import CreateSessionRequest, DeploymentProvider, DeploymentStatus


class ProviderError(Exception):
    pass


class ProviderConfigError(ProviderError):
    pass


@dataclass(frozen=True)
class ProviderRefresh:
    status: DeploymentStatus | None = None
    url: str | None = None
    progress_percent: int | None = None
    provider_patch: dict[str, Any] | None = None


class DeploymentAdapter(ABC):
    provider: DeploymentProvider

    @abstractmethod
    def provision(
        self,
        deployment_id: str,
        user_id: str,
        request: CreateSessionRequest,
        public_host: str | None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def refresh(self, deployment: dict[str, Any]) -> ProviderRefresh:
        del deployment
        return ProviderRefresh()

    def destroy(self, deployment: dict[str, Any]) -> None:
        del deployment
