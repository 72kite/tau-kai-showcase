import pytest

from memory_mcp_server import crypto_store
from memory_mcp_server.profile_store import ProfileStore


def test_get_unknown_person_returns_none(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")

    assert store.get("nobody") is None


def test_upsert_defaults_access_level_to_unknown(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")

    profile = store.upsert("zion")

    assert profile == {"access_level": "unknown"}


def test_set_access_level_updates_and_persists(tmp_path):
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.upsert("zion")

    store.set_access_level("zion", "owner")

    reloaded = ProfileStore(path)
    assert reloaded.get("zion")["access_level"] == "owner"


def test_upsert_merges_additional_fields(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")

    store.upsert("zion", name="Zion")
    profile = store.upsert("zion", access_level="owner")

    assert profile == {"access_level": "owner", "name": "Zion"}


def test_list_all_returns_every_profile(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")
    store.upsert("zion")
    store.upsert("guest", access_level="visitor")

    assert store.list_all() == {
        "zion": {"access_level": "unknown"},
        "guest": {"access_level": "visitor"},
    }


def test_list_all_on_empty_store_returns_empty_dict(tmp_path):
    store = ProfileStore(tmp_path / "profiles.json")

    assert store.list_all() == {}


# --- Phase 10: encryption at rest ---------------------------------------------------------------


def test_stays_plaintext_without_a_master_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TAU_MASTER_KEY", raising=False)
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.upsert("zion", name="Zion")

    raw = path.read_bytes()
    assert not raw.startswith(crypto_store._MAGIC)
    assert b"Zion" in raw


def test_encrypts_when_master_key_set_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "correct horse battery staple")
    path = tmp_path / "profiles.json"

    store1 = ProfileStore(path)
    store1.upsert("zion", name="Zion", access_level="owner")

    raw = path.read_bytes()
    assert raw.startswith(crypto_store._MAGIC)
    assert b"Zion" not in raw

    store2 = ProfileStore(path)
    assert store2.get("zion") == {"access_level": "owner", "name": "Zion"}


def test_wrong_master_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "right passphrase")
    path = tmp_path / "profiles.json"
    store = ProfileStore(path)
    store.upsert("zion")

    monkeypatch.setenv("TAU_MASTER_KEY", "wrong passphrase")
    with pytest.raises(ValueError):
        ProfileStore(path)
