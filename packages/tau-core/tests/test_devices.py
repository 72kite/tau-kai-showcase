"""Phase 6.D: DeviceRegistry bookkeeping (pure, no FastAPI). Phase 27.A adds device-approval
state, bearer tokens, and optional persistence - still pure, still no FastAPI."""

import os

import pytest

from tau_core.web.devices import DeviceRegistry


def test_touch_creates_minimal_entry_with_fallback_name():
    reg = DeviceRegistry()
    dev = reg.touch("abcdef123456")
    assert dev is not None
    assert dev.device_id == "abcdef123456"
    assert dev.name == "device-abcdef12"  # short fallback so admin list has no blank rows
    assert dev.first_seen and dev.last_seen


def test_touch_ignores_anonymous_client():
    reg = DeviceRegistry()
    assert reg.touch("") is None
    assert reg.touch("   ") is None
    assert reg.list() == []


def test_register_sets_name_and_touch_updates_last_seen():
    reg = DeviceRegistry()
    reg.register("dev1", "Kitchen iPad")
    assert reg.get("dev1").name == "Kitchen iPad"
    first = reg.get("dev1").last_seen
    # A later touch without a name keeps the name but advances last_seen.
    reg.touch("dev1")
    assert reg.get("dev1").name == "Kitchen iPad"
    assert reg.get("dev1").last_seen >= first


def test_register_rejects_empty_id():
    reg = DeviceRegistry()
    with pytest.raises(ValueError):
        reg.register("", "nameless")


def test_list_is_most_recently_seen_first():
    reg = DeviceRegistry()
    reg.register("old", "Old")
    reg.register("new", "New")
    reg.touch("new")  # make 'new' most recent
    names = [d.device_id for d in reg.list()]
    assert names[0] == "new"
    assert set(names) == {"old", "new"}


# --- Phase 27.A: approval state, tokens, persistence -------------------------------------------


def test_new_device_starts_pending_with_no_token():
    reg = DeviceRegistry()
    dev = reg.touch("dev1")
    assert dev.status == "pending"
    assert dev.token_hash is None


def test_approve_unknown_device_returns_none():
    reg = DeviceRegistry()
    assert reg.approve("never-seen") is None


def test_approve_mints_token_and_flips_status():
    reg = DeviceRegistry()
    reg.touch("dev1")
    token = reg.approve("dev1")
    assert token and len(token) > 20  # secrets.token_urlsafe(32) output
    dev = reg.get("dev1")
    assert dev.status == "approved"
    assert dev.token_hash is not None
    assert dev.token_hash != token  # never stores the raw token


def test_verify_token_accepts_the_minted_token():
    reg = DeviceRegistry()
    reg.touch("dev1")
    token = reg.approve("dev1")
    assert reg.verify_token("dev1", token) is True


def test_verify_token_rejects_wrong_token():
    reg = DeviceRegistry()
    reg.touch("dev1")
    reg.approve("dev1")
    assert reg.verify_token("dev1", "totally-wrong-token") is False


def test_verify_token_rejects_unknown_or_unapproved_device():
    reg = DeviceRegistry()
    assert reg.verify_token("never-seen", "anything") is False
    reg.touch("dev1")  # pending, never approved
    assert reg.verify_token("dev1", "anything") is False


def test_verify_token_rejects_blocked_device_even_with_right_token():
    reg = DeviceRegistry()
    reg.touch("dev1")
    token = reg.approve("dev1")
    reg.block("dev1")
    assert reg.verify_token("dev1", token) is False


def test_as_dict_never_exposes_token_hash():
    reg = DeviceRegistry()
    reg.touch("dev1")
    reg.approve("dev1")
    data = reg.get("dev1").as_dict()
    assert "token_hash" not in data
    assert data["status"] == "approved"


def test_reapproving_a_blocked_device_cannot_get_a_fresh_token_by_reregistering():
    """A blocked device re-registering under the same id must not silently regain trust just by
    touching the registry again - only an explicit unblock (or a fresh approve) changes that,
    and approve() itself does not clear `blocked`."""
    reg = DeviceRegistry()
    reg.touch("dev1")
    reg.approve("dev1")
    reg.block("dev1")
    reg.touch("dev1", name="renamed")  # re-registering / re-announcing
    assert reg.get("dev1").blocked is True


def test_persistence_round_trip_plaintext(tmp_path):
    store_path = tmp_path / "devices.json"
    reg1 = DeviceRegistry(store_path=store_path)
    reg1.touch("dev1")
    token = reg1.approve("dev1")
    assert store_path.exists()  # approve() is the persisting write

    reg2 = DeviceRegistry(store_path=store_path)
    dev1 = reg2.get("dev1")
    assert dev1 is not None
    assert dev1.status == "approved"
    assert reg2.verify_token("dev1", token) is True


def test_touch_alone_is_lost_on_restart_with_no_approve_in_between(tmp_path):
    """touch() never writes to disk by itself (see module docstring) - a pending device that no
    approve() snapshot ever ran alongside is lost across a restart, same as before persistence
    existed. It simply re-touches itself on its next request."""
    store_path = tmp_path / "devices.json"
    reg1 = DeviceRegistry(store_path=store_path)
    reg1.touch("dev1")  # never approved, no save ever triggered

    reg2 = DeviceRegistry(store_path=store_path)
    assert reg2.get("dev1") is None


def test_persistence_round_trip_encrypted(tmp_path):
    store_path = tmp_path / "devices.json"
    key = os.urandom(32)
    reg1 = DeviceRegistry(store_path=store_path, encryption_key=key)
    reg1.touch("dev1")
    token = reg1.approve("dev1")

    raw = store_path.read_bytes()
    assert b"dev1" not in raw  # not plaintext on disk

    reg2 = DeviceRegistry(store_path=store_path, encryption_key=key)
    assert reg2.verify_token("dev1", token) is True

    # Wrong key can't decrypt - the store must not silently look empty or corrupt state; it
    # should refuse to load and fall back to an empty registry rather than raising out of
    # __init__ (mirrors PendingActionQueue's corrupt-store handling).
    reg3 = DeviceRegistry(store_path=store_path, encryption_key=os.urandom(32))
    assert reg3.get("dev1") is None


def test_blank_store_path_stays_in_memory():
    reg = DeviceRegistry(store_path=None)
    reg.touch("dev1")
    reg.approve("dev1")  # must not raise with no store configured
    assert reg.get("dev1").status == "approved"
