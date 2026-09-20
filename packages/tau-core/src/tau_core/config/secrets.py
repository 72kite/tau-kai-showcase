from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values


class SecretsProvider(Protocol):
    """Pluggable secret lookup so Tau Core doesn't hard-code where credentials come from.

    Phase 0 calls for HashiCorp Vault; that backend can be dropped in later behind this same
    interface without touching any calling code. For now, EnvFileSecretsProvider lets Phase 1
    development proceed without Vault standing up first.
    """

    def get(self, key: str) -> str | None: ...

    def require(self, key: str) -> str: ...


class EnvFileSecretsProvider:
    """Reads secrets from a local .env file, falling back to the process environment."""

    def __init__(self, env_path: str | Path = ".env"):
        self._path = Path(env_path)
        self._values: dict[str, str | None] = dotenv_values(self._path) if self._path.exists() else {}

    def get(self, key: str) -> str | None:
        return self._values.get(key) or os.environ.get(key)

    def require(self, key: str) -> str:
        value = self.get(key)
        if value is None:
            raise KeyError(f"Required secret '{key}' not found in {self._path} or the environment")
        return value
