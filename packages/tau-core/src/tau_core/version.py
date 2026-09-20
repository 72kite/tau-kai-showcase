"""Where Tau's version number comes from.

Until now nothing in this repo reported a version: every pyproject.toml carried a hardcoded
`0.1.0` that no code read, and there were no git tags. That is fine until several kiosks are on a
wall running different builds, at which point "what is this box actually running" has no answer.

Two values, deliberately separate:

- **version** - the package's semantic version, read from the installed distribution metadata so
  `pyproject.toml` stays the single source of truth rather than a constant that drifts from it.
- **build** - which commit that came from. Passed in through the environment (`TAU_BUILD_SHA`) by
  scripts/bring-up-tau.ps1 and docker-compose's build args, *not* read from git here: `.git` is
  not in the Docker build context, so asking git at runtime inside a container would yield nothing
  and stamp every image "unknown".

Both degrade to a readable placeholder rather than raising. A missing version must never be able
to take down the bridge - it is diagnostic metadata, not a dependency.
"""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version as _dist_version

UNKNOWN_VERSION = "0.0.0+unknown"
DEFAULT_BUILD = "dev"


def tau_core_version() -> str:
    """tau-core's semantic version, from installed distribution metadata.

    Returns UNKNOWN_VERSION when tau-core isn't pip-installed - i.e. when running straight from a
    source checkout, which is exactly how the tests and the chat REPL run it.
    """
    try:
        return _dist_version("tau-core")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


def build_sha() -> str:
    """The commit this build came from, or 'dev' when nobody stamped it."""
    return os.environ.get("TAU_BUILD_SHA") or DEFAULT_BUILD


def version_info() -> dict[str, str]:
    """The pair, as reported by /api/health and utility-mcp-server's tau://identity."""
    return {"version": tau_core_version(), "build": build_sha()}
