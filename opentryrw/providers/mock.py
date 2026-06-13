from __future__ import annotations

from typing import Any

from opentryrw.models import CreateSessionRequest, DeploymentProvider

from .base import DeploymentAdapter


class MockProvider(DeploymentAdapter):
    provider = DeploymentProvider.mock

    def provision(
        self,
        deployment_id: str,
        user_id: str,
        request: CreateSessionRequest,
        public_host: str | None,
    ) -> dict[str, Any]:
        del user_id, request, public_host
        return {
            "provider": self.provider.value,
            "url": f"https://{deployment_id[:6]}.demo.open.local",
        }
