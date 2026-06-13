# OpenTryRW

OpenTryRW is an open source launcher pattern for temporary Remnawave demo
instances. It is intentionally not a pixel clone of `try.rw`; the goal is to
provide a clean implementation plan and a usable frontend prototype for the same
core workflow.

## Current Prototype

Run the API:

```powershell
uv run uvicorn opentryrw.api:app --host 127.0.0.1 --port 4174
```

Run the frontend dev server in another terminal:

```powershell
cd frontend
npm install
npm run dev
```

Then open:

- App: <http://127.0.0.1:5173/>
- Swagger: <http://127.0.0.1:4174/docs>
- OpenAPI JSON: <http://127.0.0.1:4174/openapi.json>

The app currently includes:

- FastAPI backend with Swagger/OpenAPI
- React, React Router, Mantine, TypeScript, and Framer Motion frontend SPA
- Pydantic API models
- PostgreSQL production storage with TinyDB local fallback
- Telegram OIDC login with PKCE/state and JWT validation
- Logout
- Version selection: stable or dev
- API-backed session creation and termination
- DigitalOcean deployment provider with cloud-init user data
- Mock deployment provider for local development
- PostgreSQL-backed deployment job queue and worker process
- Provider resource tracking and audit/deployment events
- Worker reconciliation loop for stale cleanup jobs/resources
- Provider registry instead of provider-specific branching in session logic
- Optional Cloudflare proxied DNS records for deployment URLs, with `sslip.io` fallback
- Telegram notification side effects, stored in the configured database and sent when bot settings exist
- Provisioning states with filled-letter progress: initializing, installing, deploying
- Ready state with access URL and one-hour countdown
- Manual termination after a server-side 10 minute lock
- Per-user session cooldown, 24 hours by default
- Background cleanup loop that enqueues expired-session cleanup jobs
- Durable rate limits for auth/session actions
- Fingerprint cooldowns to reduce Telegram-account cycling abuse

## Target Flow

1. User authenticates with Telegram.
2. Backend exchanges the Telegram authorization code and validates `id_token`.
3. Backend creates a short-lived session token or HTTP-only cookie.
4. User requests a temporary Remnawave instance.
5. Worker provisions infrastructure and reports status.
6. Console polls session state until the instance is ready.
7. User receives a Telegram DM with the access URL.
8. Operators receive a Telegram log message.
9. Instance is automatically terminated after the TTL.
10. User receives a termination DM and cannot request another session until the cooldown expires.

## Storage Choice

PostgreSQL is the production storage backend. TinyDB remains available only as a
lightweight local fallback through `STORAGE_BACKEND=tinydb`.

## Container

Local production-style run:

```powershell
Copy-Item .env.example .env
# edit .env
docker compose up --build
```

Production image run:

```powershell
Copy-Item .env.example .env
# edit .env
docker compose -f docker-compose.prod.yml up -d
```

`docker-compose.yml` maps the real application settings from `.env` into the
container and starts PostgreSQL. In compose, `STORAGE_BACKEND=postgres` is the
default and the app entrypoint runs `alembic upgrade head` before starting
Uvicorn. Compose also starts `opentryrw-worker`, which consumes deployment jobs
and performs provider lifecycle operations outside request handling. The local
`.env` file is ignored by git and Docker build context. TinyDB remains available
for lightweight local fallback outside compose. The included GitHub Actions
workflows build the container and publish frontend release bundles. Push the
`frontend` tag for a production frontend bundle or the `frontend_dev` tag for a
prerelease bundle. The container Dockerfile builds the SPA and copies
`frontend/dist` into the backend image, where FastAPI serves it with a SPA
fallback.

Frontend development:

```powershell
cd frontend
npm install
npm run dev
```

The Vite dev server proxies `/api` to `http://127.0.0.1:4174`.

Operational endpoints:

- Health: `/api/health`
- Metrics: `/api/metrics`
- Readiness: `/api/readiness`

See [docs/operations-runbook.md](./docs/operations-runbook.md) for production
start, migration, backup, and emergency-stop commands. Use
[docs/beta-launch-checklist.md](./docs/beta-launch-checklist.md) before the first
real-provider beta session.

## Deployment Implementation

The active provider is selected server-side through `DEPLOYMENT_PROVIDER`.
The mock provider remains available for local UI/API work.

DigitalOcean flow:

1. Create a Droplet through the DigitalOcean API.
2. Pass cloud-init user data during Droplet creation.
3. Install Docker, Caddy, and Remnawave on first boot.
4. Render a safe allowlist of Remnawave `.env` variables.
5. Expose the panel through Caddy.
6. If Cloudflare is configured, create a proxied A record for a generated
   subdomain. Otherwise use `<droplet-ip>.sslip.io`.
7. Poll the Droplet and health-check the panel URL.
8. Destroy the Droplet and DNS record at manual termination or TTL cleanup.

DigitalOcean user data cannot be changed after Droplet creation, so Remnawave
environment settings must be collected before deployment.

Cloudflare subdomains are generated as:

```text
<mammal_animal>-<capital_of_the_country>-<short_uuid_6_symbols>
```

The generator enforces DNS limits: each label is at most 63 characters and the
full domain is at most 255 characters.

## Configuration

Set these environment variables for Telegram auth:

- `TELEGRAM_CLIENT_ID`
- `TELEGRAM_CLIENT_SECRET`
- `TELEGRAM_REDIRECT_URI`, defaults to `<FRONTEND_ORIGIN>/api/auth/telegram/callback`
- `FRONTEND_ORIGIN`, defaults to `http://127.0.0.1:4174`

Set these environment variables for Telegram notifications:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_OPERATOR_CHAT_ID`

Set these environment variables for real deployments:

- `DEPLOYMENT_PROVIDER=digitalocean`
- `DIGITALOCEAN_TOKEN`
- `DIGITALOCEAN_REGION`, defaults to `fra1`
- `DIGITALOCEAN_SIZE`, defaults to `s-1vcpu-2gb`
- `DIGITALOCEAN_IMAGE`, defaults to `ubuntu-24-04-x64`
- `DIGITALOCEAN_SSH_KEYS`, optional comma-separated key IDs or fingerprints
- `DEPLOYMENT_PUBLIC_DOMAIN_TEMPLATE`, defaults to `{ip}.sslip.io`
- `REMNAWAVE_STABLE_COMPOSE_URL`
- `REMNAWAVE_DEV_COMPOSE_URL`
- `REMNAWAVE_ENV_SAMPLE_URL`

Set these environment variables for Cloudflare proxied deployment URLs:

- `CLOUDFLARE_API_TOKEN`
- `CLOUDFLARE_ZONE_ID`
- `CLOUDFLARE_ROOT_DOMAIN`, for example `demo.example.com`
- `CLOUDFLARE_RECORD_TTL`, defaults to `1` for automatic TTL

Supported Remnawave environment controls:

- Telegram notifications: `IS_TELEGRAM_NOTIFICATIONS_ENABLED`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_NOTIFY_*`
- Documentation: `IS_DOCS_ENABLED`, with fixed `/docs` and `/scalar` paths
- Webhook: `WEBHOOK_ENABLED`, `WEBHOOK_URL`, `WEBHOOK_SECRET_HEADER`

Operational settings:

- `DATABASE_URL`, defaults to `postgresql+asyncpg://opentryrw:opentryrw@postgres:5432/opentryrw`
- `STORAGE_BACKEND`, use `postgres` for production and `tinydb` for lightweight local fallback
- `RUN_MIGRATIONS`, defaults to `true` in the Docker entrypoint
- `COOKIE_SECURE`, set to `true` behind HTTPS in production
- `SESSION_COOLDOWN_SECONDS`, defaults to `86400`
- `FINGERPRINT_COOLDOWN_SECONDS`, defaults to `SESSION_COOLDOWN_SECONDS`
- `MAX_ACTIVE_INSTANCES`, defaults to `10`; use `0`, `inf`, or `unlimited` for no limit
- `CLEANUP_INTERVAL_SECONDS`, defaults to `30`
- `RECONCILIATION_INTERVAL_SECONDS`, defaults to `300`
- `STALE_RESOURCE_GRACE_SECONDS`, defaults to `900`
- `WORKER_ID`, defaults to `opentryrw-worker`
- `WORKER_POLL_INTERVAL_SECONDS`, defaults to `5`
- `WORKER_BATCH_SIZE`, defaults to `4`
- `WORKER_LOCK_SECONDS`, defaults to `120`
- `ABUSE_HASH_SECRET`, HMAC secret for IP/browser fingerprint hashes
- `TRUST_PROXY_HEADERS`, defaults to `false`; enable only behind a trusted reverse proxy
- `RATE_LIMIT_AUTH_START_PER_MINUTE`, defaults to `12`
- `RATE_LIMIT_AUTH_CALLBACK_PER_MINUTE`, defaults to `20`
- `RATE_LIMIT_SESSION_CREATE_PER_HOUR`, defaults to `6`
- `RATE_LIMIT_SESSION_DELETE_PER_MINUTE`, defaults to `10`

See [docs/architecture.md](./docs/architecture.md) for API and data model notes.
