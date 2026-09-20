from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


class AdminServerSettings(BaseSettings):
    """Config for the standalone admin control panel backend (Phase 38). Deliberately its own
    env prefix (TAU_ADMIN_*, not tau-core's TAU_*) - this is a separate process with its own
    identity, even when both read from the same host's environment."""

    model_config = SettingsConfigDict(env_prefix="TAU_ADMIN_", env_file=".env", extra="ignore")

    # Where the admin credential lives (Phase 38: a single admin account, not a user-management
    # system - "a separate sign-in", not a roster). Durable by default, same reasoning as every
    # other auth-bearing store in this project: losing the account on every restart would force
    # re-bootstrapping constantly. Encrypted at rest when TAU_MASTER_KEY is set (crypto_store.py).
    credential_store_path: Path = PACKAGE_ROOT / "data" / "admin_credential.json"
    # Where live session tokens persist - so a restart doesn't silently log out whoever is mid-use.
    # Only a sha256 hash of each token is ever written, same as DeviceRegistry.
    session_store_path: Path = PACKAGE_ROOT / "data" / "admin_sessions.json"
    session_ttl_seconds: float = 12 * 60 * 60  # 12h - long enough for a work session

    # Bootstrap: creates the admin account on first run ONLY (credential_store_path doesn't exist
    # yet). Ignored on every later run, even if still set in .env - this is a one-time seed, not a
    # way to reset the password (use POST /login then POST /change-password for that). None means
    # "don't bootstrap" - a fresh install with neither an existing credential store nor these two
    # set simply has no admin account yet and every route 401s until one is created some other way.
    bootstrap_username: str | None = None
    bootstrap_password: str | None = None

    # Where tau-core itself lives, and the shared secret that satisfies its _require_admin gate
    # (TAU_ADMIN_SERVICE_TOKEN over there - the two names deliberately match so pointing one at
    # the other is a copy-paste, not a translation). This service is useless without both set;
    # left optional here (not validated at startup) so tests can construct settings without them.
    tau_core_base_url: str = "http://localhost:8000"
    tau_core_service_token: str | None = None
    tau_core_request_timeout_seconds: float = 15.0
    # Bearer token for the synthetic "tau-admin-server" device (proxy.py's fixed X-Tau-Device-Id)
    # - minted by approving that device from tau-core's own admin dashboard once
    # (POST /api/admin/devices/tau-admin-server/approve), same as any real kiosk. None (the
    # default) sends no X-Tau-Device-Token at all, which is fine when TAU_REQUIRE_DEVICE_TOKEN is
    # off over on tau-core, but 403s the drafts promote/discard passthrough once it's on - which
    # is the default there since Phase 48. Not validated at startup, same reasoning as
    # tau_core_service_token above: this service should still boot and serve everything gated by
    # _require_admin alone even with this unset.
    tau_core_device_token: str | None = None

    # Same LAN-only default posture as tau-core's own CORS handling, but this surface is more
    # sensitive (it changes what Tau does, not just reads/chats with it) - operators are expected
    # to additionally restrict reachability at the network layer (bind to a VPN-only interface,
    # Docker network, or reverse-proxy path), not rely on CORS as the boundary. Empty means "no
    # browser origin is allowed" (safer default than tau-core's LAN-regex, since this is a new,
    # separate attack surface being introduced deliberately narrow); set explicitly for the real
    # admin frontend's origin.
    allowed_origins: str = ""

    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]
