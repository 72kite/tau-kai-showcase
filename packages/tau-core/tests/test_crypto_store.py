"""Phase 10: encryption-at-rest for JSON stores holding sensitive data (voiceprints, in this
package's case, via the approval queue). Covers the crypto_store module directly and its wiring
into PendingActionQueue - a store must round-trip correctly when TAU_MASTER_KEY is set, must stay
plaintext (today's behavior, unchanged) when it's unset, and must fail loudly rather than silently
returning garbage on a wrong key or a tampered file.
"""

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tau_core import crypto_store
from tau_core.approval import ApprovalStatus, PendingActionQueue


def test_no_master_key_means_plaintext_on_disk(tmp_path):
    """Unset TAU_MASTER_KEY == today's behavior. A fresh install/CI run needs no setup."""
    store = tmp_path / "approvals.json"
    queue = PendingActionQueue(store_path=store, encryption_key=None)
    queue.submit(server="s", tool="t", arguments={"a": 1}, reason="r", requested_by="u")

    raw = store.read_bytes()
    assert not raw.startswith(crypto_store._MAGIC)
    assert b'"tool": "t"' in raw


def test_encrypted_store_is_opaque_on_disk_and_round_trips(tmp_path):
    store = tmp_path / "approvals.json"
    key = crypto_store._derive_key_cached("correct horse battery staple", str(store) + ".salt")

    queue = PendingActionQueue(store_path=store, encryption_key=key)
    request = queue.submit(server="s", tool="exit_lockdown", arguments={"a": 1}, reason="r", requested_by="u")

    raw = store.read_bytes()
    assert raw.startswith(crypto_store._MAGIC)
    assert b"exit_lockdown" not in raw

    restarted = PendingActionQueue(store_path=store, encryption_key=key)
    restored = restarted.get(request.id)
    assert restored.tool == "exit_lockdown"
    assert restored.status is ApprovalStatus.PENDING


def test_wrong_key_fails_loudly_instead_of_silently_returning_garbage(tmp_path):
    store = tmp_path / "approvals.json"
    right_key = crypto_store._derive_key_cached("right passphrase", str(store) + ".salt")
    queue = PendingActionQueue(store_path=store, encryption_key=right_key)
    queue.submit(server="s", tool="t", arguments={}, reason="r", requested_by="u")

    wrong_key = AESGCM.generate_key(bit_length=256)
    with pytest.raises(ValueError):
        crypto_store.read_json(store, wrong_key)


def test_tampered_ciphertext_fails_loudly(tmp_path):
    store = tmp_path / "approvals.json"
    key = crypto_store._derive_key_cached("a passphrase", str(store) + ".salt")
    queue = PendingActionQueue(store_path=store, encryption_key=key)
    queue.submit(server="s", tool="t", arguments={}, reason="r", requested_by="u")

    raw = bytearray(store.read_bytes())
    raw[-1] ^= 0xFF  # flip the last byte of the GCM tag/ciphertext
    store.write_bytes(bytes(raw))

    with pytest.raises(ValueError):
        crypto_store.read_json(store, key)


def test_encrypted_file_without_a_key_raises_a_clear_error(tmp_path):
    store = tmp_path / "approvals.json"
    key = crypto_store._derive_key_cached("a passphrase", str(store) + ".salt")
    queue = PendingActionQueue(store_path=store, encryption_key=key)
    queue.submit(server="s", tool="t", arguments={}, reason="r", requested_by="u")

    with pytest.raises(ValueError, match="TAU_MASTER_KEY"):
        crypto_store.read_json(store, None)


def test_resolve_key_is_none_when_env_var_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("TAU_MASTER_KEY", raising=False)
    assert crypto_store.resolve_key(tmp_path / "approvals.json") is None


def test_resolve_key_derives_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "a passphrase")
    store = tmp_path / "approvals.json"
    key1 = crypto_store.resolve_key(store)
    key2 = crypto_store.resolve_key(store)
    assert key1 is not None
    assert key1 == key2  # same passphrase + salt path -> cached, identical key
    assert (tmp_path / "approvals.json.salt").exists()
