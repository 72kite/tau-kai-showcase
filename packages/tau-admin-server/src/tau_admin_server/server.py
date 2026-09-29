"""The standalone admin control panel backend (Phase 38).

Deliberately its own process, port, and auth stack, separate from tau-core's kiosk-facing bridge:
before this, "admin" meant a spoken-phrase voice challenge (or nothing at all, when
TAU_REQUIRE_VOICE_APPROVAL was off - the common default) riding the same process and port as
chat/voice, reachable by anything on the LAN. This service puts a real username/password login in
front of the surface that changes how Tau behaves (device approval, wake word, draft-memory
review, security posture), and calls back into tau-core with a shared service-token credential
(TAU_ADMIN_SERVICE_TOKEN on both sides) rather than asking every admin action to also carry a
voice token. The kiosk frontend no longer links here at all - see App.jsx/TopBar.jsx.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tau_admin_server import crypto_store
from tau_admin_server.credentials import AdminCredentialStore
from tau_admin_server.proxy import TauCoreError, TauCoreProxy
from tau_admin_server.sessions import SessionStore
from tau_admin_server.settings import AdminServerSettings

logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ApproveWakeWordRequest(BaseModel):
    wake_id: str
    approval_request_id: str | None = None


class ApproveVoiceRequest(BaseModel):
    voice_id: str
    approval_request_id: str | None = None


class DraftActionRequest(BaseModel):
    approval_request_id: str | None = None


class SetAccessLevelRequest(BaseModel):
    access_level: str
    approval_request_id: str | None = None


class ApprovalDecisionRequest(BaseModel):
    note: str | None = None


def create_app(
    settings: AdminServerSettings | None = None,
    credentials: AdminCredentialStore | None = None,
    sessions: SessionStore | None = None,
    proxy: TauCoreProxy | None = None,
) -> FastAPI:
    settings = settings or AdminServerSettings()

    if credentials is None:
        cred_key = (
            crypto_store.resolve_key(settings.credential_store_path)
            if settings.credential_store_path is not None
            else None
        )
        credentials = AdminCredentialStore(settings.credential_store_path, encryption_key=cred_key)

    if sessions is None:
        session_key = (
            crypto_store.resolve_key(settings.session_store_path)
            if settings.session_store_path is not None
            else None
        )
        sessions = SessionStore(
            settings.session_store_path, encryption_key=session_key, ttl_seconds=settings.session_ttl_seconds
        )

    if proxy is None:
        proxy = TauCoreProxy(
            base_url=settings.tau_core_base_url,
            service_token=settings.tau_core_service_token,
            timeout_seconds=settings.tau_core_request_timeout_seconds,
            device_token=settings.tau_core_device_token,
        )

    # First-run bootstrap only - see settings.py's docstring on why this never re-runs once a
    # credential store exists, even if the env vars are still set.
    if settings.bootstrap_username and settings.bootstrap_password and not credentials.exists():
        credentials.bootstrap(settings.bootstrap_username, settings.bootstrap_password)
        logger.warning(
            "Bootstrapped admin account '%s' from TAU_ADMIN_BOOTSTRAP_* env vars. Unset those "
            "now - they have no further effect, and change the password with POST /change-password.",
            settings.bootstrap_username,
        )
    elif not credentials.exists():
        logger.warning(
            "No admin account exists yet and no TAU_ADMIN_BOOTSTRAP_USERNAME/PASSWORD were set - "
            "every route below /login will 401 until one is created."
        )

    if not settings.tau_core_service_token:
        logger.warning(
            "TAU_ADMIN_SERVICE_TOKEN is unset - every proxied call to tau-core will fail unless "
            "tau-core is in LAN-trust mode (TAU_REQUIRE_VOICE_APPROVAL=false over there)."
        )

    app = FastAPI(title="Tau Admin Server")

    origins = settings.cors_origins()
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def _bearer(authorization: str | None) -> str:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="Missing bearer token")
        return authorization.split(" ", 1)[1].strip()

    def _current_user(authorization: str | None = Header(default=None)) -> str:
        token = _bearer(authorization)
        username = sessions.verify(token)
        if username is None:
            raise HTTPException(status_code=401, detail="Session invalid or expired")
        return username

    def _current_token(authorization: str | None = Header(default=None)) -> str:
        return _bearer(authorization)

    async def _proxied(coro):
        try:
            return await coro
        except TauCoreError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        except Exception as exc:  # noqa: BLE001 - network/timeout errors reaching tau-core itself
            logger.warning("tau-core call failed: %s", exc)
            raise HTTPException(status_code=502, detail="Could not reach tau-core.") from exc

    # --- auth -------------------------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "admin_account_configured": credentials.exists()}

    @app.post("/login")
    async def login(body: LoginRequest) -> dict:
        if not credentials.verify(body.username, body.password):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        token = sessions.create(body.username)
        return {"token": token, "username": body.username}

    @app.post("/logout")
    async def logout(token: str = Depends(_current_token)) -> dict:
        sessions.revoke(token)
        return {"status": "ok"}

    @app.get("/me")
    async def me(username: str = Depends(_current_user)) -> dict:
        return {"username": username}

    @app.post("/change-password")
    async def change_password(
        body: ChangePasswordRequest, username: str = Depends(_current_user)
    ) -> dict:
        if not credentials.verify(username, body.current_password):
            raise HTTPException(status_code=403, detail="Current password is incorrect")
        credentials.set_password(username, body.new_password)
        return {"status": "ok"}

    # Route paths from here down deliberately mirror tau-core's own /api/* shapes (GET
    # /api/devices, /api/admin/system, /api/voice/wake-words, etc.) rather than a shorter local
    # scheme - the admin-frontend panels ported from the kiosk app (Phase 38) share their fetch
    # code with the old voice-token path almost unchanged, just swapping auth headers and origin.

    # --- devices ------------------------------------------------------------------------------

    @app.get("/api/devices")
    async def list_devices(_: str = Depends(_current_user)) -> list[dict]:
        return await _proxied(proxy.list_devices())

    @app.post("/api/admin/devices/{device_id}/approve")
    async def approve_device(device_id: str, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.approve_device(device_id))

    @app.post("/api/admin/devices/{device_id}/block")
    async def block_device(device_id: str, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.block_device(device_id))

    @app.post("/api/admin/devices/{device_id}/unblock")
    async def unblock_device(device_id: str, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.unblock_device(device_id))

    @app.delete("/api/admin/devices/{device_id}")
    async def remove_device(device_id: str, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.remove_device(device_id))

    # --- snapshots ----------------------------------------------------------------------------

    @app.get("/api/admin/system")
    async def system(_: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.system_snapshot())

    @app.get("/api/admin/people")
    async def people(_: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.people_snapshot())

    @app.get("/api/admin/security")
    async def security(_: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.security_snapshot())

    @app.get("/api/transcript/unified")
    async def transcript_unified(_: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.unified_transcript())

    @app.get("/api/activity")
    async def activity(limit: int = 50, _: str = Depends(_current_user)) -> list[dict]:
        return await _proxied(proxy.activity_feed(limit))

    # --- people / access levels -----------------------------------------------------------------

    @app.post("/api/people/{person_id}/access-level")
    async def set_access_level(
        person_id: str, body: SetAccessLevelRequest, _: str = Depends(_current_user)
    ) -> dict:
        return await _proxied(proxy.set_access_level(person_id, body.access_level, body.approval_request_id))

    # --- draft memory review --------------------------------------------------------------------
    # NOT a mirror of tau-core's own shape here: on tau-core, promote/discard ride the generic
    # /api/tools/memory-mcp-server/{tool} passthrough, gated only by _device_id() (device-token
    # enforcement, off by default) - not _require_admin at all. These two routes close that gap
    # for the admin panel specifically: both require a real logged-in session, proxy.py attaches
    # the passthrough call underneath. admin-frontend's draft review is a new component, not a
    # port of DraftReviewPanel.jsx, because of this difference.

    @app.get("/api/drafts")
    async def drafts(_: str = Depends(_current_user)) -> list[dict]:
        return await _proxied(proxy.list_drafts())

    @app.post("/api/drafts/{node_id}/promote")
    async def promote_draft(
        node_id: str, body: DraftActionRequest = DraftActionRequest(), _: str = Depends(_current_user)
    ) -> dict:
        return await _proxied(proxy.promote_draft(node_id, body.approval_request_id))

    @app.post("/api/drafts/{node_id}/discard")
    async def discard_draft(node_id: str, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.discard_draft(node_id))

    # --- wake word ----------------------------------------------------------------------------

    @app.get("/api/voice/wake-words")
    async def wake_words(_: str = Depends(_current_user)) -> list[dict]:
        return await _proxied(proxy.list_wake_words())

    @app.post("/api/voice/wake-words")
    async def set_wake_word(body: ApproveWakeWordRequest, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.set_wake_word(body.wake_id, body.approval_request_id))

    # --- spoken voice -------------------------------------------------------------------------
    @app.get("/api/voice/voices")
    async def voices(_: str = Depends(_current_user)) -> list[dict]:
        return await _proxied(proxy.list_voices())

    @app.post("/api/voice/voices")
    async def set_voice(body: ApproveVoiceRequest, _: str = Depends(_current_user)) -> dict:
        return await _proxied(proxy.set_voice(body.voice_id, body.approval_request_id))

    # --- approvals ------------------------------------------------------------------------------
    # Phase 41: previously the only way to decide a pending CDG-gated action was the kiosk's
    # ApprovalQueue overlay - an admin working from this panel could see (via WakeWordPanel/
    # DraftsPanel/UserProfilesPanel's own "approve, then Continue" note) that something was
    # blocked, but not act on it without leaving the page. decided_by is always stamped from the
    # logged-in session here, never accepted from the client - the audit trail on tau-core's side
    # should name a real admin account, not a string anyone with a valid session could spoof.

    @app.get("/api/approvals")
    async def approvals(_: str = Depends(_current_user)) -> list[dict]:
        return await _proxied(proxy.list_approvals())

    @app.post("/api/approvals/{request_id}/approve")
    async def approve_pending(
        request_id: str,
        body: ApprovalDecisionRequest = ApprovalDecisionRequest(),
        username: str = Depends(_current_user),
    ) -> dict:
        return await _proxied(proxy.approve_action(request_id, f"admin:{username}", body.note))

    @app.post("/api/approvals/{request_id}/deny")
    async def deny_pending(
        request_id: str,
        body: ApprovalDecisionRequest = ApprovalDecisionRequest(),
        username: str = Depends(_current_user),
    ) -> dict:
        return await _proxied(proxy.deny_action(request_id, f"admin:{username}", body.note))

    return app
