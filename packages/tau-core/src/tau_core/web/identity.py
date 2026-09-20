"""Challenge-response voice identity for elevated actions (project-tau-plan.md, "Proposed:
multi-user voice identification").

The threat this addresses is REPLAY: someone playing a recording of an authorized person's
voice. A random challenge phrase defeats it - a recording can't predict the phrase, and a
different person reading the phrase fails the voiceprint match. Passive playback-artifact
detection (ASVspoof-class models) is deliberately not attempted; challenge-response is both
stronger and explainable.

This module is pure bookkeeping (phrases, expiry, scoring) so it's unit-testable without any
audio stack. The bridge (server.py) does the actual transcription/voiceprint calls through
host.call_tool and feeds the results in here.

Voice identity NEVER acts alone: what an identity may do is enforced by the CDG and the
approval queue; a verified challenge only proves "this person, live, just now" - it does not
bypass any approval requirement, it satisfies the who-is-approving question.
"""

from __future__ import annotations

import re
import secrets
import time
import uuid
from dataclasses import dataclass, field

# Short, phonetically distinct, easy to say and easy for Whisper to get right. Four of these
# give ~1.6M combinations - far beyond what a replay attacker can pre-record.
CHALLENGE_WORDS = (
    "amber breeze copper delta ember falcon garnet harbor indigo jasper "
    "kestrel lantern marble nectar orchid pepper quartz river saddle timber "
    "umber velvet walnut yonder zephyr anchor bishop candle dagger engine"
).split()

CHALLENGE_TTL_SECONDS = 60
TOKEN_TTL_SECONDS = 120
# Fraction of challenge words that must appear in the spoken transcript. Not 1.0: Whisper
# occasionally drops one word of four from room-distance speech; 0.75 still requires 3 exact
# rare-word hits, which a stale recording cannot produce.
WORD_MATCH_THRESHOLD = 0.75


def _normalize(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower()))


def phrase_match_score(challenge_phrase: str, spoken_transcript: str) -> float:
    """Fraction of challenge words present in the transcript (order-insensitive: Whisper
    punctuation/ordering noise shouldn't fail a genuinely live speaker)."""
    wanted = _normalize(challenge_phrase)
    if not wanted:
        return 0.0
    heard = _normalize(spoken_transcript)
    return len(wanted & heard) / len(wanted)


@dataclass
class _Challenge:
    phrase: str
    issued_at: float
    verified_person: str | None = None
    verified_access_level: str | None = None
    token: str | None = None
    token_issued_at: float = 0.0
    consumed: bool = False


@dataclass
class ChallengeStore:
    """In-memory, single-process store. Challenges are ephemeral by design - a bridge restart
    invalidating outstanding challenges is correct behavior, not data loss."""

    _challenges: dict[str, _Challenge] = field(default_factory=dict)

    def create(self) -> tuple[str, str]:
        """Issue a new challenge. Returns (challenge_id, phrase)."""
        self._prune()
        phrase = " ".join(secrets.choice(CHALLENGE_WORDS) for _ in range(4))
        challenge_id = uuid.uuid4().hex
        self._challenges[challenge_id] = _Challenge(phrase=phrase, issued_at=time.time())
        return challenge_id, phrase

    def get_phrase(self, challenge_id: str) -> str | None:
        challenge = self._challenges.get(challenge_id)
        if challenge is None or self._expired(challenge):
            return None
        return challenge.phrase

    def mark_verified(
        self, challenge_id: str, person_id: str, access_level: str | None = None
    ) -> str | None:
        """Records a successful (words + voiceprint) verification; returns a single-use token.

        The verified access level is frozen into the challenge here (for the token's 120s life)
        so the approval path can gate on tier without a second profile lookup - see
        tau_core.access.tiers for how it's consumed."""
        challenge = self._challenges.get(challenge_id)
        if challenge is None or self._expired(challenge):
            return None
        challenge.verified_person = person_id
        challenge.verified_access_level = access_level
        challenge.token = uuid.uuid4().hex
        challenge.token_issued_at = time.time()
        return challenge.token

    def consume_token(self, token: str) -> tuple[str, str | None] | None:
        """Redeems a verification token exactly once. Returns (person_id, access_level), or None
        if the token is unknown, expired, or already used."""
        for challenge in self._challenges.values():
            if challenge.token == token:
                if challenge.consumed or time.time() - challenge.token_issued_at > TOKEN_TTL_SECONDS:
                    return None
                challenge.consumed = True
                return challenge.verified_person, challenge.verified_access_level
        return None

    def peek_token(self, token: str) -> tuple[str, str | None] | None:
        """Validate a token WITHOUT consuming it. Returns (person_id, access_level), or None if
        unknown/expired. For read-only admin access (Phase 6.D unified log, device list) that a
        dashboard may poll repeatedly within the token's 120s life - replaying a read is not a
        destructive risk, so single-use (consume_token) would just force needless re-challenges.
        Actions (approvals) still use consume_token, which is replay-resistant."""
        for challenge in self._challenges.values():
            if challenge.token == token:
                if challenge.consumed or time.time() - challenge.token_issued_at > TOKEN_TTL_SECONDS:
                    return None
                return challenge.verified_person, challenge.verified_access_level
        return None

    def _expired(self, challenge: _Challenge) -> bool:
        return time.time() - challenge.issued_at > CHALLENGE_TTL_SECONDS

    def _prune(self) -> None:
        cutoff = time.time() - max(CHALLENGE_TTL_SECONDS, TOKEN_TTL_SECONDS) * 2
        stale = [cid for cid, c in self._challenges.items() if c.issued_at < cutoff]
        for cid in stale:
            del self._challenges[cid]
