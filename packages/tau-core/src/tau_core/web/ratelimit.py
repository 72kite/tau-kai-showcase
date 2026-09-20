"""A small in-process rate limiter for the bridge's unauthenticated, expensive endpoints.

Phase 7 Tier 1 #9 flagged `/api/chat` and `/api/voice/*` specifically: unlike the read-only
resource endpoints, each call here drives a real LLM turn or a real STT/embedding inference, and
neither endpoint requires any credential. Without a bound, one buggy client (or a deliberate
flood) can peg the box - this is a bar to entry, not a security control; the CDG remains the
actual authorization boundary for what a call is allowed to *do*.

Deliberately not a token-bucket-per-second thing: a fixed sliding window keyed by client (device
id when known, else IP) is the simplest model that answers the one question that matters here -
"has this client made too many calls recently" - and it's cheap enough for a household kiosk's
handful of concurrent devices, not built for internet-scale traffic.
"""

from __future__ import annotations

import time
from collections import deque


class RateLimiter:
    """At most `max_requests` calls per `window_seconds`, per key.

    `now` is only a parameter so tests can drive the clock deterministically instead of sleeping
    for real seconds - callers in the app always let it default to `time.monotonic()`.
    """

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        if max_requests < 1:
            raise ValueError("max_requests must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = {}

    def _recent_hits(self, key: str, now: float) -> deque[float]:
        hits = self._hits.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while hits and hits[0] < cutoff:
            hits.popleft()
        return hits

    def allow(self, key: str, now: float | None = None) -> bool:
        """Records a hit and returns whether it was within budget. Rejected calls are NOT
        counted against the window - a client already being throttled shouldn't have its retry
        attempts extend its own timeout."""
        now = time.monotonic() if now is None else now
        hits = self._recent_hits(key, now)
        if len(hits) >= self.max_requests:
            return False
        hits.append(now)
        return True

    def retry_after(self, key: str, now: float | None = None) -> float:
        """Seconds until the oldest hit in the current window ages out, i.e. until `allow` would
        next return True. 0.0 for a key with no recent hits (nothing to wait on)."""
        now = time.monotonic() if now is None else now
        hits = self._recent_hits(key, now)
        if not hits:
            return 0.0
        return max(0.0, self.window_seconds - (now - hits[0]))
