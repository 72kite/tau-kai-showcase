"""Tests for tau_core.version - where Tau's version/build stamp comes from.

Small module, but two of its behaviours are load-bearing and easy to regress:

- It must never raise. It is diagnostic metadata read on a request path; a missing version taking
  down the bridge would be absurd.
- Its "I don't know" answers must be *recognisable placeholders*, because the kiosk footer decides
  whether to show a STALE drift warning by comparing these values. If a source checkout reported
  something that merely looked like a real version, every dev machine would show a permanent false
  alarm - and a warning that is always on is a warning nobody reads.
"""

from importlib.metadata import PackageNotFoundError

import pytest

from tau_core import version as version_mod
from tau_core.version import (
    DEFAULT_BUILD,
    UNKNOWN_VERSION,
    build_sha,
    tau_core_version,
    version_info,
)


def test_build_sha_reads_the_environment(monkeypatch):
    monkeypatch.setenv("TAU_BUILD_SHA", "a3f9c21")
    assert build_sha() == "a3f9c21"


def test_build_sha_falls_back_when_unstamped(monkeypatch):
    monkeypatch.delenv("TAU_BUILD_SHA", raising=False)
    assert build_sha() == DEFAULT_BUILD


def test_build_sha_treats_empty_as_unstamped(monkeypatch):
    # Compose interpolates an unset variable to an empty string rather than omitting it, so ""
    # arrives here in practice; it means "nobody stamped this", not a build literally named "".
    monkeypatch.setenv("TAU_BUILD_SHA", "")
    assert build_sha() == DEFAULT_BUILD


def test_version_is_reported_when_installed(monkeypatch):
    monkeypatch.setattr(version_mod, "_dist_version", lambda _name: "1.2.3")
    assert tau_core_version() == "1.2.3"


def test_version_falls_back_when_not_installed(monkeypatch):
    """Running from a source checkout (tests, the chat REPL) has no distribution metadata."""

    def _raise(_name):
        raise PackageNotFoundError("tau-core")

    monkeypatch.setattr(version_mod, "_dist_version", _raise)
    assert tau_core_version() == UNKNOWN_VERSION


def test_unknown_version_is_marked_so_the_footer_can_ignore_it():
    # The frontend's drift check keys off this suffix (see useVersion.js). If the sentinel ever
    # stops being recognisable as "unknown", every source-run bridge starts reporting false drift.
    assert UNKNOWN_VERSION.endswith("+unknown")


def test_version_info_pairs_both(monkeypatch):
    monkeypatch.setattr(version_mod, "_dist_version", lambda _name: "0.1.0")
    monkeypatch.setenv("TAU_BUILD_SHA", "deadbee")
    assert version_info() == {"version": "0.1.0", "build": "deadbee"}


@pytest.mark.parametrize("fn", [tau_core_version, build_sha, version_info])
def test_never_raises_in_a_clean_environment(monkeypatch, fn):
    monkeypatch.delenv("TAU_BUILD_SHA", raising=False)
    fn()  # must not raise
