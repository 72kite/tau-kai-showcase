"""Optional encryption-at-rest for JSON stores holding sensitive data (voiceprints, proposals,
person profiles, incident logs). Duplicated (not shared) across six packages - tau-core,
phase4-upgrade-pipeline, memory-mcp-server, ui-bridge-mcp-server, security-mcp-server and
tau-admin-server - because each has its own venv and a Docker build context scoped to its own
directory, so a shared internal package would be invisible to five of the image builds. The build
context is what decides this, not preference.

Keep the six copies identical. CI enforces it: the `crypto-store-parity` job compares every copy's
AST with docstrings stripped, so the CODE cannot drift even though each copy is free to localize
its own prose. A genuine behaviour change here means changing all six, deliberately.

Plaintext by default (TAU_MASTER_KEY unset): existing installs and tests need zero setup, matching
how `require_voice_approval` defaults off "so a fresh install can't lock itself out" rather than
failing closed. Setting TAU_MASTER_KEY turns encryption on for whichever store passes its
derived key in - there is no recovery if the passphrase is lost, since Argon2id is one-way and
nothing else remembers it.
"""

from __future__ import annotations

import json
import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from argon2.low_level import Type, hash_secret_raw
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_MAGIC = b"TAUENC1"
_SALT_BYTES = 16
_KEY_BYTES = 32
_NONCE_BYTES = 12

# Argon2id cost params: tuned for roughly 100-300ms on modest hardware. Callers MUST go through
# resolve_key()'s cache rather than deriving per-call - this is deliberately slow (that's the
# point of a KDF), and memory-mcp-server's _profiles() constructs a fresh ProfileStore on every
# tool call, so an uncached derivation here would add real per-request latency.
_ARGON2_TIME_COST = 3
_ARGON2_MEMORY_COST_KIB = 65536  # 64 MiB
_ARGON2_PARALLELISM = 2


def _salt_path_for(store_path: Path) -> Path:
    return store_path.with_name(store_path.name + ".salt")


def _get_or_create_salt(salt_path: Path) -> bytes:
    if salt_path.exists():
        return salt_path.read_bytes()
    salt = secrets.token_bytes(_SALT_BYTES)
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(salt)
    return salt


@lru_cache(maxsize=None)
def _derive_key_cached(passphrase: str, salt_path_str: str) -> bytes:
    salt = _get_or_create_salt(Path(salt_path_str))
    return hash_secret_raw(
        secret=passphrase.encode("utf-8"),
        salt=salt,
        time_cost=_ARGON2_TIME_COST,
        memory_cost=_ARGON2_MEMORY_COST_KIB,
        parallelism=_ARGON2_PARALLELISM,
        hash_len=_KEY_BYTES,
        type=Type.ID,
    )


def resolve_key(store_path: Path, env_var: str = "TAU_MASTER_KEY") -> bytes | None:
    """Derives the encryption key for `store_path` from `env_var`, or returns None if that env
    var isn't set (plaintext mode). Cached, so repeated calls for the same passphrase+store are
    cheap after the first Argon2id derivation."""
    passphrase = os.environ.get(env_var)
    if not passphrase:
        return None
    return _derive_key_cached(passphrase, str(_salt_path_for(Path(store_path))))


def read_json(path: Path, key: bytes | None) -> Any:
    """Reads and decodes a JSON file that may or may not be encrypted. Auto-detects: a file
    written by write_json_bytes() with a key starts with a magic prefix; anything else (including
    every store written before encryption was turned on) is read as plain JSON. This is also the
    transparent upgrade path - an existing plaintext store reads normally, then gets re-saved
    encrypted the next time something writes it, once TAU_MASTER_KEY is set."""
    raw = Path(path).read_bytes()
    if raw.startswith(_MAGIC):
        if key is None:
            raise ValueError(
                f"{path} is encrypted but no key was provided (TAU_MASTER_KEY not set?) - cannot decrypt"
            )
        body = raw[len(_MAGIC) :]
        nonce, ciphertext = body[:_NONCE_BYTES], body[_NONCE_BYTES:]
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, None)
        except InvalidTag as exc:
            # Wrong passphrase or a tampered/corrupted file - both look identical to an AEAD
            # cipher. Normalized to ValueError so callers already handling a corrupt JSON store
            # (e.g. PendingActionQueue._load's `except (OSError, ValueError, ...)`) also catch
            # this without needing to know about cryptography's exception types.
            raise ValueError(f"{path}: could not decrypt (wrong TAU_MASTER_KEY or corrupted file)") from exc
        return json.loads(plaintext.decode("utf-8"))
    return json.loads(raw.decode("utf-8"))


def write_json_bytes(data: Any, key: bytes | None) -> bytes:
    """Serializes `data` to the bytes that should be written to disk - plain JSON if `key` is
    None, else AES-256-GCM-encrypted (authenticated, so a tampered file fails to decrypt loudly
    instead of being silently accepted)."""
    payload = json.dumps(data, indent=2, default=str).encode("utf-8")
    if key is None:
        return payload
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, payload, None)
    return _MAGIC + nonce + ciphertext
