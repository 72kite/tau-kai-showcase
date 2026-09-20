"""Pure logic tests for tau_core.web.ratelimit.RateLimiter (Phase 7 Tier 1 #9)."""

import pytest

from tau_core.web.ratelimit import RateLimiter


def test_allows_up_to_max_requests_within_window():
    limiter = RateLimiter(max_requests=3, window_seconds=10.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("a", now=1.0) is True
    assert limiter.allow("a", now=2.0) is True
    assert limiter.allow("a", now=3.0) is False


def test_rejected_calls_do_not_extend_the_window():
    """A client already over budget shouldn't have its retry attempts pinned to a moving
    target - only successful hits count toward the window."""
    limiter = RateLimiter(max_requests=1, window_seconds=10.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("a", now=1.0) is False
    assert limiter.allow("a", now=2.0) is False
    # The original hit at t=0 ages out at t=10, not pushed later by the rejected attempts.
    assert limiter.allow("a", now=10.5) is True


def test_window_slides_and_old_hits_age_out():
    limiter = RateLimiter(max_requests=2, window_seconds=5.0)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("a", now=1.0) is True
    assert limiter.allow("a", now=2.0) is False
    # t=0 hit ages out at t=5, freeing a slot.
    assert limiter.allow("a", now=5.1) is True


def test_keys_are_independent():
    limiter = RateLimiter(max_requests=1, window_seconds=10.0)
    assert limiter.allow("device:a", now=0.0) is True
    assert limiter.allow("device:b", now=0.0) is True
    assert limiter.allow("device:a", now=0.5) is False
    assert limiter.allow("device:b", now=0.5) is False


def test_retry_after_counts_down_to_the_oldest_hit_aging_out():
    limiter = RateLimiter(max_requests=1, window_seconds=10.0)
    limiter.allow("a", now=0.0)
    assert limiter.retry_after("a", now=3.0) == pytest.approx(7.0)
    assert limiter.retry_after("a", now=10.0) == 0.0


def test_retry_after_is_zero_for_a_key_with_no_hits():
    limiter = RateLimiter(max_requests=1, window_seconds=10.0)
    assert limiter.retry_after("never-seen", now=0.0) == 0.0


def test_rejects_nonpositive_configuration():
    with pytest.raises(ValueError):
        RateLimiter(max_requests=0, window_seconds=10.0)
    with pytest.raises(ValueError):
        RateLimiter(max_requests=1, window_seconds=0.0)
