"""Pure unit tests for SessionRegistry (Phase 12, per-device sessions).

No host, no async, no Ollama - the registry is deliberately a small pure data structure
(factory + LRU dict) so its isolation and eviction guarantees can be pinned without the whole
LLM stack, mirroring how test_ratelimit.py pins the RateLimiter.
"""

import pytest

from tau_core.session import SessionManager, SessionRegistry


def _factory():
    """A factory that stamps each session's id with its key, so a test can prove which key a
    returned session belongs to."""
    return lambda key: SessionManager(session_id=f"device:{key}")


def test_same_key_returns_the_same_session():
    reg = SessionRegistry(_factory())
    first = reg.get_or_create("kitchen")
    second = reg.get_or_create("kitchen")
    assert first is second


def test_different_keys_get_isolated_sessions():
    reg = SessionRegistry(_factory())
    kitchen = reg.get_or_create("kitchen")
    study = reg.get_or_create("study")
    assert kitchen is not study

    kitchen.add_message("user", "kitchen secret")
    # The isolation guarantee: one device's history never appears in another's.
    assert [m.content for m in study.history()] == []
    assert [m.content for m in kitchen.history()] == ["kitchen secret"]


def test_get_does_not_create_or_reorder():
    reg = SessionRegistry(_factory())
    assert reg.get("never-seen") is None
    reg.get_or_create("a")
    assert len(reg) == 1  # get() of an absent key created nothing


def test_lru_eviction_past_the_cap():
    reg = SessionRegistry(_factory(), max_sessions=2)
    reg.get_or_create("a")
    reg.get_or_create("b")
    reg.get_or_create("c")  # evicts "a", the least-recently-used
    assert len(reg) == 2
    assert reg.get("a") is None
    assert reg.get("b") is not None
    assert reg.get("c") is not None


def test_touching_a_key_saves_it_from_eviction():
    reg = SessionRegistry(_factory(), max_sessions=2)
    a = reg.get_or_create("a")
    reg.get_or_create("b")
    # Re-touch "a" so it's now most-recently-used; "b" becomes the eviction target.
    assert reg.get_or_create("a") is a
    reg.get_or_create("c")  # evicts "b", not "a"
    assert reg.get("b") is None
    assert reg.get("a") is a


def test_evicted_key_starts_fresh_on_return():
    reg = SessionRegistry(_factory(), max_sessions=1)
    a = reg.get_or_create("a")
    a.add_message("user", "remembered")
    reg.get_or_create("b")  # evicts "a"
    a_again = reg.get_or_create("a")
    # A returning evicted device gets a brand-new empty session, not its old history.
    assert a_again is not a
    assert a_again.history() == []


def test_max_sessions_must_be_at_least_one():
    with pytest.raises(ValueError):
        SessionRegistry(_factory(), max_sessions=0)
