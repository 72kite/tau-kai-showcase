# tau-admin-server

Standalone backend for Tau's admin control panel (Phase 38). Separate process, separate port,
separate authentication from `tau-core`'s kiosk-facing bridge - a real username/password login
instead of a spoken voice challenge (or the LAN-trust bypass tau-core falls back to when voice
approval is off). The kiosk frontend has no link to it at all; it's reached as its own app.

## Why a separate service

Before this, "admin" access rode the same process and port as chat/voice, gated by a voice token
that (a) needed someone enrolled for voice at all, and (b) was skipped entirely under the common
default (`TAU_REQUIRE_VOICE_APPROVAL=false`). The admin surface - device approval, wake-word
switching, draft-memory review, security posture - changes how Tau behaves, which is a different
risk tier than reading a transcript, and deserved its own credential and its own ability to be
network-restricted independently of the always-on assistant.

## How it talks to tau-core

Every proxied call carries `X-Tau-Admin-Service-Token`, a static shared secret configured
identically on both sides (`TAU_ADMIN_SERVICE_TOKEN` in tau-core's `.env`,
`TAU_ADMIN_TAU_CORE_SERVICE_TOKEN` here). tau-core's `_require_admin` gate accepts this credential
exactly as it would an admin voice token - see `tau_core/web/server.py`. This service does its own
independent human authentication (the password login below) and never asks an admin to also
complete a voice challenge.

## Routes

- `POST /login` `{username, password}` → `{token, username}`
- `POST /logout`, `GET /me`, `POST /change-password` (all require `Authorization: Bearer <token>`)
- `GET /health` (no auth - for the compose healthcheck)

Every route below requires the bearer session and deliberately mirrors tau-core's own `/api/*`
path shapes (not a shorter local scheme), so admin-frontend panels ported from the kiosk app share
their fetch code almost unchanged - just a different origin and auth header:

- `GET /api/devices`, `POST /api/admin/devices/{id}/approve|block|unblock`
- `GET /api/admin/system`, `/api/admin/people`, `/api/admin/security`, `/api/transcript/unified`
- `GET /api/drafts`, `POST /api/drafts/{id}/promote` (two-stage: retry with `approval_request_id`
  once a human approves the resulting card), `POST /api/drafts/{id}/discard` - **not** a mirror of
  tau-core's own shape: on tau-core these ride the generic, only device-token-gated
  `/api/tools/memory-mcp-server/{tool}` passthrough; these two routes require a real admin session
  instead, closing that gap for this panel specifically.
- `GET /api/voice/wake-words`, `POST /api/voice/wake-words` (same two-stage approval shape)

## First run

Set `TAU_ADMIN_BOOTSTRAP_USERNAME`/`TAU_ADMIN_BOOTSTRAP_PASSWORD` in `.env` for the first boot
only - the account is created once and those vars are ignored on every later run, even if still
set (see `settings.py`). Change the password afterward with `POST /change-password`; there is
deliberately no "reset" endpoint.

## Running it

```
pip install -e ".[dev]"
python -m tau_admin_server   # binds 0.0.0.0:8100 by default
```

## Testing

```
pytest
```

`test_auth.py` covers login/session/bootstrap/encryption-at-rest with no tau-core involved.
`test_proxy.py` exercises `TauCoreProxy`'s HTTP behavior against a fake transport. `test_proxy_routes.py`
covers every route end to end with a fake `TauCoreProxy` injected via `create_app(proxy=...)`.
