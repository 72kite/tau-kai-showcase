"""Optional encryption-at-rest for JSON stores holding sensitive data (the admin credential and
session stores). Duplicated (not shared) from tau-core's tau_core/crypto_store.py - see that
file's own docstring for why (each package has its own venv/Docker build context; a shared
internal package would be invisible to five of the image builds). This copy's prose is localized to
the stores it actually serves; its CODE must stay identical to the other five, which CI's
`crypto-store-parity` job enforces by comparing ASTs with docstrings stripped.

Plaintext by default (TAU_MASTER_KEY unset): a fresh install needs zero setup. Setting
TAU_MASTER_KEY turns encryption on for whichever store passes its derived key in - there is no
recovery if the passphrase is lost, since Argon2id is one-way and nothing else remembers it.
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
    written by write_json_bytes() with a key starts with a magic prefix; anything else is read
    as plain JSON."""
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
