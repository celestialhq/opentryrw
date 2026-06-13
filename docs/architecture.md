# OpenTryRW Architecture Notes

These notes capture the observed product behavior and translate it into an
open source implementation plan.

## UX States

### Public Landing

- Brand and short product promise
- Telegram login button
- Four-step explanation:
  - request access
  - wait for setup
  - get URL
  - use the instance for one hour

### Console: No Active Session

- Version selector:
  - stable
  - dev
- Advanced Remnawave environment settings:
  - Telegram notifications
  - Documentation
  - Webhook
- Deploy button
- Static service facts:
  - session duration: 1 hour
  - setup time: 3-5 minutes

### Console: Provisioning

- Large status headline: setting up instance
- Status word rotates through:
  - initializing
  - installing
  - deploying
- Page updates automatically from session polling
- Toast confirms session creation

### Console: Ready

- Ready headline
- Open Remnawave button
- Time remaining countdown
- Terminate instance button, disabled until the server-side terminate lock expires

### Console: Cooldown

- Cooldown headline
- Countdown until the next allowed session
- No deploy controls while cooldown is active

Telegram operator/user notifications are backend side effects. They should not
be rendered inside the user console.

## API Contract

The backend is FastAPI. Swagger is served at `/docs`; OpenAPI JSON is served at
`/openapi.json`.

Suggested public API for the open source version:

```http
GET /api/auth/status
GET /api/auth/telegram/start
GET /api/auth/telegram/callback
POST /api/auth/logout

GET /api/session
POST /api/session
DELETE /api/session

POST /api/webhooks/deployment
```

### `GET /api/auth/status`

Returns product metadata needed by the landing page.

```json
{
  "response": {
    "authenticated": false,
    "user": null,
    "telegram_auth_enabled": true
  }
}
```

### `GET /api/auth/telegram/start`

Starts Telegram OIDC login. The backend creates:

- random `state`
- PKCE `code_verifier`
- authorization URL at `https://oauth.telegram.org/auth`

The frontend keeps a custom designed button and redirects to this endpoint.

### `GET /api/auth/telegram/callback`

Accepts Telegram OIDC `code` and `state`. The backend must:

- consume and validate `state`
- exchange `code` for tokens with Telegram
- validate `id_token` signature through Telegram JWKS
- verify issuer, audience and expiry

Prefer returning a `Set-Cookie` header with an HTTP-only session cookie:

```http
Set-Cookie: opentryrw_session=...; HttpOnly; Secure; SameSite=Lax; Path=/
```

An SPA bearer token can be supported as a fallback, but it should not be the
only production auth mode.

### `GET /api/session`

Returns the active session and control flags.

```json
{
  "response": {
    "session": {
      "id": "29564923-449b-4d90-bf18-81f755d68c5a",
      "version": "stable",
      "status": "deploying",
      "url": null,
      "started_at": 1781247240000,
      "ready_at": 1781247420000,
      "expires_at": 1781250840000,
      "progress_percent": 67,
      "can_terminate": false
    },
    "cooldown_until": 1781333640000,
    "can_create_session": false
  }
}
```

### `POST /api/session`

Creates a deployment request.

```json
{
  "version": "stable",
  "remnawave": {
    "documentation": {
      "enabled": true
    },
    "telegram_notifications": {
      "enabled": false
    },
    "webhook": {
      "enabled": true,
      "url": "https://example.com/webhook",
      "secret_header": "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
    }
  }
}
```

Expected side effects:

- create session row
- create or refresh the per-user cooldown row
- enqueue provisioning job
- send operator Telegram notification
- respond with current session

If the user has no active session and the cooldown is still active, return
`429` with the cooldown timestamp.

### `DELETE /api/session`

Terminates the active session if the backend allows it.

Expected side effects:

- enqueue destroy job or call provider cleanup directly
- mark session as terminating
- send user/operator termination notifications

The backend rejects manual termination until the terminate lock expires.

If the service-wide capacity is full, the backend returns `429`:

```json
{
  "detail": {
    "message": "Service capacity is full",
    "active": 10,
    "limit": 10
  }
}
```

## Session Statuses

Recommended enum:

- `queued`
- `initializing`
- `installing`
- `deploying`
- `ready`
- `terminating`
- `terminated`
- `failed`

## Data Model

```sql
create table users (
  id uuid primary key,
  telegram_id bigint unique not null,
  username text,
  first_name text,
  last_name text,
  created_at timestamptz not null default now()
);

create table sessions (
  id uuid primary key,
  user_id uuid not null references users(id),
  version text not null,
  provider text not null,
  provider_instance_id text,
  provider_region text,
  status text not null,
  access_url text,
  error_message text,
  created_at timestamptz not null default now(),
  ready_at timestamptz,
  expires_at timestamptz not null,
  terminated_at timestamptz
);

create table cooldowns (
  user_id uuid primary key references users(id),
  cooldown_until timestamptz not null
);
```

## Telegram Notifications

Operator log supports Telegram topics. Configure:

```text
TELEGRAM_OPERATOR_CHAT_ID=-1001234567890:42
```

Without `:42`, the message is sent to the group root.

Ready operator log:

```text
🦋 OpenTryRW instance just got deployed.

🪪 Instance ID: 2956...8c5a
🆔 User ID: 9*******
🚀 Deployed in 2 minutes.

🟢 2/10 (2 instances active out of 10)
```

User DM:

```text
👋 OpenTryRW session has been created.

🪪 Session ID: 29564923-449b-4d90-bf18-81f755d68c5a
🔗 Access URL: https://...

🕘 Session will be deleted in 59 minutes.
```

Termination operator log:

```text
☠️ OpenTryRW instance has been terminated.

🪪 Instance ID: 2956...8c5a

🟡 0/10 (0 instances active out of 10)
```

Termination user DM:

```text
👋 OpenTryRW session has been terminated.

🪪 Session ID: 29564923-449b-4d90-bf18-81f755d68c5a

Thanks for using OpenTryRW. We hope you had a great experience.

🦋 Join Remnawave

To create new session, use OpenTryRW.
```

## Provisioning Worker

The code uses a provider registry with a real DigitalOcean adapter and a mock
adapter behind the same API. Provider adapters own infrastructure lifecycle.
The shared endpoint layer owns public host generation, Cloudflare DNS records,
and `sslip.io` fallback. The real worker should be idempotent. Every step checks
the current session status before acting.

1. Mark session as `initializing`.
2. Create DigitalOcean Droplet with tags and cloud-init `user_data`.
3. Install Docker, Caddy, and Remnawave during first boot.
4. Render only the safe Remnawave `.env` allowlist.
5. Expose panel URL through Cloudflare proxied DNS when configured.
6. Fall back to `<droplet-ip>.sslip.io` if Cloudflare is not configured or record creation fails.
7. Poll Droplet status and panel health.
8. Mark session `ready`.
9. Schedule cleanup for `expires_at`.

DigitalOcean user data is immutable after Droplet creation, so all Remnawave
environment settings must be validated and rendered before the Droplet is
created.

Supported Remnawave `.env` controls:

- `IS_TELEGRAM_NOTIFICATIONS_ENABLED`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_NOTIFY_USERS`
- `TELEGRAM_NOTIFY_NODES`
- `TELEGRAM_NOTIFY_CRM`
- `TELEGRAM_NOTIFY_SERVICE`
- `TELEGRAM_NOTIFY_TBLOCKER`
- `IS_DOCS_ENABLED`
- `SWAGGER_PATH=/docs`
- `SCALAR_PATH=/scalar`
- `WEBHOOK_ENABLED`
- `WEBHOOK_URL`
- `WEBHOOK_SECRET_HEADER`

## Cleanup

Cleanup must be enforced server-side even if the user closes the browser.
The current mock backend includes a background cleanup loop that scans expired
sessions and runs termination side effects.

Recommended options:

- queue delayed job keyed by session id
- scheduler scanning expired active sessions
- provider-side TTL labels as a final safety net

## Security Notes

- Do not trust frontend session duration.
- Validate Telegram OIDC tokens only on the backend.
- Prefer HTTP-only cookies over localStorage bearer tokens.
- Store provider credentials as secrets.
- Mask Telegram IDs in public/operator logs unless full IDs are required.
- Rate limit session creation per Telegram account and per IP.
