"""Login, session, and password-change behavior - no tau-core involved (nothing here calls the
proxy)."""

import httpx
import pytest
from httpx import ASGITransport

from tau_admin_server.credentials import AdminCredentialStore
from tau_admin_server.server import create_app
from tau_admin_server.sessions import SessionStore
from tau_admin_server.settings import AdminServerSettings


def make_app(tmp_path, bootstrap=True):
    settings = AdminServerSettings(
        credential_store_path=tmp_path / "credential.json",
        session_store_path=tmp_path / "sessions.json",
        bootstrap_username="zion" if bootstrap else None,
        bootstrap_password="correct horse battery staple" if bootstrap else None,
        tau_core_service_token="shhh-secret",
    )
    app = create_app(settings=settings)
    return app


def client_for(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_bootstrap_creates_account_and_login_succeeds(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        r = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
    assert r.status_code == 200
    body = r.json()
    assert body["username"] == "zion"
    assert isinstance(body["token"], str) and len(body["token"]) > 20


async def test_login_wrong_password_is_401(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        r = await client.post("/login", json={"username": "zion", "password": "wrong"})
    assert r.status_code == 401


async def test_login_unknown_username_is_401(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        r = await client.post("/login", json={"username": "nope", "password": "whatever"})
    assert r.status_code == 401


async def test_no_bootstrap_means_login_always_fails(tmp_path):
    app = make_app(tmp_path, bootstrap=False)
    async with client_for(app) as client:
        r = await client.post("/login", json={"username": "zion", "password": "anything"})
    assert r.status_code == 401


async def test_bootstrap_is_one_time_only(tmp_path):
    """A second app instance pointed at the same store with the same bootstrap env vars must not
    reset the password - the whole point of "not a way to reset the password" (server.py)."""
    settings = AdminServerSettings(
        credential_store_path=tmp_path / "credential.json",
        session_store_path=tmp_path / "sessions.json",
        bootstrap_username="zion",
        bootstrap_password="first-password",
    )
    create_app(settings=settings)  # first boot creates the account

    settings2 = AdminServerSettings(
        credential_store_path=tmp_path / "credential.json",
        session_store_path=tmp_path / "sessions.json",
        bootstrap_username="zion",
        bootstrap_password="second-password",  # would-be reset attempt
    )
    app2 = create_app(settings=settings2)
    async with client_for(app2) as client:
        old = await client.post("/login", json={"username": "zion", "password": "first-password"})
        new = await client.post("/login", json={"username": "zion", "password": "second-password"})
    assert old.status_code == 200
    assert new.status_code == 401


async def test_protected_route_requires_bearer_token(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        r = await client.get("/me")
    assert r.status_code == 401


async def test_me_with_valid_token(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        login = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
        token = login.json()["token"]
        r = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "zion"


async def test_logout_invalidates_the_token(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        login = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
        token = login.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get("/me", headers=headers)).status_code == 200

        out = await client.post("/logout", headers=headers)
        assert out.status_code == 200

        after = await client.get("/me", headers=headers)
        assert after.status_code == 401


async def test_change_password_then_old_password_stops_working(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        login = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}

        r = await client.post(
            "/change-password",
            json={"current_password": "correct horse battery staple", "new_password": "new-pass-123"},
            headers=headers,
        )
        assert r.status_code == 200

        old_login = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
        assert old_login.status_code == 401
        new_login = await client.post("/login", json={"username": "zion", "password": "new-pass-123"})
        assert new_login.status_code == 200


async def test_change_password_requires_correct_current_password(tmp_path):
    app = make_app(tmp_path)
    async with client_for(app) as client:
        login = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        r = await client.post(
            "/change-password",
            json={"current_password": "wrong-current", "new_password": "new-pass-123"},
            headers=headers,
        )
    assert r.status_code == 403


# --- encryption at rest --------------------------------------------------------------------------


async def test_credential_store_is_encrypted_when_master_key_set(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "a-strong-passphrase")
    app = make_app(tmp_path)
    async with client_for(app) as client:
        r = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
    assert r.status_code == 200
    raw = (tmp_path / "credential.json").read_bytes()
    assert raw.startswith(b"TAUENC1")
    assert b"zion" not in raw


# --- sessions surviving a restart -----------------------------------------------------------------


async def test_sessions_persist_across_app_restarts(tmp_path):
    settings = AdminServerSettings(
        credential_store_path=tmp_path / "credential.json",
        session_store_path=tmp_path / "sessions.json",
        bootstrap_username="zion",
        bootstrap_password="correct horse battery staple",
    )
    app1 = create_app(settings=settings)
    async with client_for(app1) as client:
        login = await client.post(
            "/login", json={"username": "zion", "password": "correct horse battery staple"}
        )
        token = login.json()["token"]

    # A brand-new app instance, same store paths - simulating a process restart.
    settings2 = AdminServerSettings(
        credential_store_path=tmp_path / "credential.json",
        session_store_path=tmp_path / "sessions.json",
    )
    app2 = create_app(settings=settings2)
    async with client_for(app2) as client:
        r = await client.get("/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "zion"


async def test_expired_session_is_rejected():
    store = SessionStore(ttl_seconds=-1)  # already expired the instant it's created
    token = store.create("zion")
    assert store.verify(token) is None
