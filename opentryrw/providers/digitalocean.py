from __future__ import annotations

import base64
import json
import shlex
import time
import urllib.error
import urllib.request
from typing import Any

from opentryrw.models import CreateSessionRequest, DeploymentProvider, DeploymentStatus, DeploymentVersion
from opentryrw.settings import settings

from .base import DeploymentAdapter, ProviderConfigError, ProviderError, ProviderRefresh

DIGITALOCEAN_API = "https://api.digitalocean.com/v2"


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        del req, fp, code, msg, headers, newurl
        return None


class DigitalOceanHTTPError(ProviderError):
    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        self.status_code = status_code
        message = f"DigitalOcean API request failed: {method} {path} returned {status_code}"
        if body:
            message = f"{message} {body}"
        super().__init__(message)


class DigitalOceanProvider(DeploymentAdapter):
    provider = DeploymentProvider.digitalocean

    def provision(
        self,
        deployment_id: str,
        user_id: str,
        request: CreateSessionRequest,
        public_host: str | None,
    ) -> dict[str, Any]:
        if not settings.digitalocean_token:
            raise ProviderConfigError("DIGITALOCEAN_TOKEN is required for DigitalOcean deployments")

        region = settings.digitalocean_region
        name = f"opentryrw-{deployment_id[:8]}"
        tags = [
            settings.digitalocean_tag_prefix,
            f"{settings.digitalocean_tag_prefix}:session:{deployment_id}",
            f"{settings.digitalocean_tag_prefix}:user:{user_id}",
        ]
        payload: dict[str, Any] = {
            "name": name,
            "region": region,
            "size": settings.digitalocean_size,
            "image": settings.digitalocean_image,
            "monitoring": True,
            "tags": tags,
            "user_data": render_remnawave_cloud_init(request, public_host),
        }
        if settings.digitalocean_ssh_keys:
            payload["ssh_keys"] = settings.digitalocean_ssh_keys

        response = digitalocean_request("POST", "/droplets", payload)
        droplet = response["droplet"]
        return {
            "provider": self.provider.value,
            "provider_instance_id": str(droplet["id"]),
            "provider_name": name,
            "provider_region": region,
            "provider_status": droplet.get("status", "new"),
            "provider_last_checked_at": 0,
            "provider_public_host": public_host,
            "url": f"https://{public_host}" if public_host else None,
        }

    def refresh(self, deployment: dict[str, Any]) -> ProviderRefresh:
        now = _now_ms()
        last_checked = int(deployment.get("provider_last_checked_at") or 0)
        if now - last_checked < settings.provider_refresh_interval_seconds * 1000:
            return ProviderRefresh()

        droplet_id = deployment.get("provider_instance_id")
        if not droplet_id:
            return ProviderRefresh(status=DeploymentStatus.failed)

        try:
            droplet = digitalocean_request("GET", f"/droplets/{droplet_id}")["droplet"]
        except DigitalOceanHTTPError as exc:
            if exc.status_code == 404:
                return ProviderRefresh(status=DeploymentStatus.terminated)
            raise ProviderError("DigitalOcean droplet refresh failed") from exc

        provider_patch: dict[str, Any] = {
            "provider_last_checked_at": now,
            "provider_status": droplet.get("status"),
        }
        public_ip = public_ipv4_from_droplet(droplet)
        if public_ip:
            provider_patch["provider_public_ip"] = public_ip

        if droplet.get("status") != "active" or not public_ip:
            return ProviderRefresh(
                status=DeploymentStatus.initializing,
                progress_percent=15,
                provider_patch=provider_patch,
            )

        if not caddy_default_page_reachable(public_ip):
            return ProviderRefresh(
                status=DeploymentStatus.installing,
                progress_percent=min(45, 20 + round(max(0, (now - int(deployment["started_at"])) / 5000))),
                provider_patch=provider_patch,
            )

        return ProviderRefresh(
            status=DeploymentStatus.deploying,
            progress_percent=min(95, 50 + round(max(0, (now - int(deployment["started_at"])) / 2000))),
            provider_patch=provider_patch,
        )

    def destroy(self, deployment: dict[str, Any]) -> None:
        errors: list[Exception] = []
        droplet_id = deployment.get("provider_instance_id")
        if droplet_id:
            try:
                digitalocean_request("DELETE", f"/droplets/{droplet_id}")
            except DigitalOceanHTTPError as exc:
                if exc.status_code != 404:
                    errors.append(exc)

        if errors:
            raise ProviderError("; ".join(str(error) for error in errors))


def render_remnawave_cloud_init(
    request: CreateSessionRequest,
    primary_host: str | None,
) -> str:
    env = remnawave_env(request)
    env_b64 = base64.b64encode(json.dumps(env, sort_keys=True).encode()).decode()
    compose_url = (
        settings.compose.stable_url
        if request.version == DeploymentVersion.stable
        else settings.compose.dev_url
    )
    public_host_assignment = (
        f"PUBLIC_HOST={shlex.quote(primary_host)}"
        if primary_host
        else "PUBLIC_HOST=\"$SSLIP_HOST\""
    )
    return f"""#cloud-config
runcmd:
  - |
    set -eux
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get upgrade -y
    apt-get install -y ca-certificates curl python3 ufw caddy
    curl -fsSL https://get.docker.com | sh

    mkdir -p /opt/remnawave
    cd /opt/remnawave
    curl -fsSLo docker-compose.yml {shlex.quote(compose_url)}
    curl -fsSLo .env {shlex.quote(settings.remnawave_env_sample_url)}

    PUBLIC_IPV4="$(curl -fsS http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address)"
    SSLIP_HOST="{settings.deployment_public_domain_template.replace('{ip}', '${PUBLIC_IPV4}')}"
    {public_host_assignment}
    export PUBLIC_HOST
    export REMNAWAVE_ENV_B64={shlex.quote(env_b64)}

    python3 - <<'PY'
    import base64
    import json
    import os
    import secrets
    from pathlib import Path

    path = Path("/opt/remnawave/.env")
    env = {{}}
    for raw in path.read_text().splitlines():
        if not raw or raw.lstrip().startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env[key] = value

    postgres_password = secrets.token_hex(24)
    env.update({{
        "JWT_AUTH_SECRET": secrets.token_hex(64),
        "JWT_API_TOKENS_SECRET": secrets.token_hex(64),
        "METRICS_PASS": secrets.token_hex(64),
        "WEBHOOK_SECRET_HEADER": secrets.token_hex(64),
        "POSTGRES_PASSWORD": postgres_password,
        "DATABASE_URL": f'"postgresql://postgres:{{postgres_password}}@remnawave-db:5432/postgres"',
        "FRONT_END_DOMAIN": os.environ["PUBLIC_HOST"],
        "SUB_PUBLIC_DOMAIN": f"{{os.environ['PUBLIC_HOST']}}/api/sub",
        "PANEL_DOMAIN": os.environ["PUBLIC_HOST"],
        "APP_PORT": "3000",
        "METRICS_PORT": "3001",
    }})
    env.update(json.loads(base64.b64decode(os.environ["REMNAWAVE_ENV_B64"]).decode()))
    path.write_text("\\n".join(f"{{key}}={{value}}" for key, value in sorted(env.items())) + "\\n")
    PY

    cat >/etc/caddy/Caddyfile <<EOF
    $PUBLIC_HOST, $SSLIP_HOST {{
      reverse_proxy 127.0.0.1:3000
    }}
    EOF

    ufw default deny incoming
    ufw default allow outgoing
    ufw allow 80/tcp
    ufw allow 443/tcp
    ufw --force enable

    systemctl enable --now caddy
    systemctl reload caddy || systemctl restart caddy
    docker compose up -d
"""


def remnawave_env(request: CreateSessionRequest) -> dict[str, str]:
    config = request.remnawave
    env: dict[str, str] = {
        "IS_DOCS_ENABLED": str(config.documentation.enabled).lower(),
        "SWAGGER_PATH": "/docs",
        "SCALAR_PATH": "/scalar",
        "IS_TELEGRAM_NOTIFICATIONS_ENABLED": str(
            config.telegram_notifications.enabled
        ).lower(),
        "WEBHOOK_ENABLED": str(config.webhook.enabled).lower(),
    }

    telegram = config.telegram_notifications
    if telegram.enabled:
        env.update(
            {
                "TELEGRAM_BOT_TOKEN": telegram.bot_token,
                "TELEGRAM_NOTIFY_USERS": telegram.notify_users,
                "TELEGRAM_NOTIFY_NODES": telegram.notify_nodes,
                "TELEGRAM_NOTIFY_CRM": telegram.notify_crm,
                "TELEGRAM_NOTIFY_SERVICE": telegram.notify_service,
                "TELEGRAM_NOTIFY_TBLOCKER": telegram.notify_tblocker,
            }
        )

    webhook = config.webhook
    if webhook.enabled and webhook.url:
        env.update(
            {
                "WEBHOOK_URL": str(webhook.url),
                "WEBHOOK_SECRET_HEADER": webhook.secret_header,
            }
        )

    return env


def digitalocean_request(
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{DIGITALOCEAN_API}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {settings.digitalocean_token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status == 204:
                return {}
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode()
        except OSError:
            body = ""
        raise DigitalOceanHTTPError(method, path, exc.code, body) from exc
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"DigitalOcean API request failed: {method} {path}") from exc


def public_ipv4_from_droplet(droplet: dict[str, Any]) -> str | None:
    for network in droplet.get("networks", {}).get("v4", []):
        if network.get("type") == "public" and network.get("ip_address"):
            return str(network["ip_address"])
    return None


def caddy_default_page_reachable(public_ip: str) -> bool:
    request = urllib.request.Request(f"http://{public_ip}", method="GET")
    opener = urllib.request.build_opener(NoRedirectHandler)
    try:
        with opener.open(request, timeout=5) as response:
            body = response.read(4096).decode(errors="ignore").lower()
            return response.status < 500 and ("caddy" in body or "congratulations" in body)
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def _now_ms() -> int:
    return int(time.time() * 1000)
