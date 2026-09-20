from __future__ import annotations

import logging

from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

# Post-turn background learning - the reliable half of "everything per-speaker" (Phase 13.5) and
# the unverified draft tier (Phase 8.B, cdg_rules.yaml's memory-tree-draft-is-allowed).
#
# draft_memory was deliberately built ungated-but-reviewable so auto-memory could be safe: writing
# a guess is free, believing it (promotion) is what needs a human. But MAIN_SYSTEM_PROMPT's own
# "Learning about the user" section only fires when the CONVERSATIONAL model notices, mid-turn,
# that something's worth remembering - competing for the same attention that's already
# demonstrably unreliable on small local models (see project-tau-plan.md's tool-selection eval
# notes), and the Phase 18 tool-free fast path has no tools at all, so it can never draft anything
# no matter how clearly the user reveals a preference in a chit-chat turn.
#
# This is a second, independent trigger: a small, narrowly-scoped classifier that looks at one
# finished turn (both paths) and decides whether it revealed a durable fact - same draft tier,
# same human-review gate at promotion, just not gated behind the main model's judgment.

_LEARNER_PROMPT = """You are a background memory scribe for a home AI called Tau. You never reply
to the user and you take no actions - you only decide whether one already-finished conversation
turn revealed something durable and worth remembering about the SPEAKER: a preference, a routine,
a name, or a decision and its reason.

should_draft = true only when the turn contains a specific, checkable fact worth recalling in a
FUTURE conversation - not small talk, not a one-off request or question, not the assistant's own
reply, not anything sensitive the speaker likely wouldn't want written down, and not anything the
speaker asked to be forgotten. When unsure, answer false: a missed fact costs nothing, a wrong one
pollutes the speaker's record.

If true, also give:
- title: a short heading, a few words (e.g. "Kitchen lighting preference")
- content: the one specific fact, phrased so a human can check it later, in plain language (e.g.
  "Prefers the kitchen lights dim after 22:00")

Draft at most one fact - the single clearest one, if several are present."""


class DraftCandidate(BaseModel):
    should_draft: bool
    title: str = ""
    content: str = ""


class PostTurnMemoryLearner:
    """One cheap router-model call per finished turn, deciding whether to draft a memory.

    Fails toward NOT drafting on any error, timeout, or malformed reply - the inverse of
    ToolNeedClassifier's safety contract, because the two costs are inverted: missing a tool
    silently hallucinates live state, but missing a memory just leaves the loop as quiet as it is
    today. A wrongly-drafted memory is still just an unverified draft nobody has to act on, so this
    errs toward the model's own should_draft judgment rather than a second-guessing regex.
    """

    def __init__(self, router_model: Model):
        self._agent = Agent(router_model, output_type=DraftCandidate, system_prompt=_LEARNER_PROMPT, retries=1)

    async def extract(self, user_text: str, assistant_reply: str) -> DraftCandidate | None:
        prompt = f"user: {user_text}\nassistant: {assistant_reply}"
        try:
            result = await self._agent.run(prompt)
        except Exception:  # noqa: BLE001 - a broken learner must never affect the turn; fail safe (no draft)
            logger.warning("Post-turn memory learner failed; skipping this turn", exc_info=True)
            return None
        return result.output
