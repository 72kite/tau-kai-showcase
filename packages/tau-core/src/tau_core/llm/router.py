from __future__ import annotations

import asyncio
import logging
import re
import statistics

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, TextOutput
from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

ROUTER_SYSTEM_PROMPT = """You are Tau's routing pre-check, not its main assistant.

You will be shown the user's message and exactly one action Tau's main model wants to take in
response - a single tool call on a single MCP server, with its arguments. Your only job is to
rate, from 0.0 to 1.0, how confident you are that this specific action is what the user actually
wants right now, and how safe/reversible it is to just do it without asking first. Do not
perform the action yourself; you have no tools.

Calibrate your score against these anchors:
- 0.9-1.0: a read-only lookup (get/list/search/status/telemetry-style tools) that plainly
  matches the request. Reading state is harmless and reversible - when the topic matches,
  score it 0.9 or higher, not a hedged middle value. This applies regardless of the domain
  being read: checking intrusion/lockdown status, camera snapshots, or VM inventories is
  still just reading - the security-sounding subject matter does not make a get_* call
  dangerous.
- 0.7-0.8: a state-changing but easily reversible action the request clearly asks for
  (turning on a light, pausing a print).
- 0.4-0.6: the action is plausibly related but relies on an assumption the message doesn't
  actually support (wrong device, invented parameter values, answering a different question).
- 0.0-0.3: destructive/irreversible actions the message doesn't explicitly ask for, or actions
  unrelated to the request.

Never give a middle score as a way to avoid deciding: if the action is a matching read-only
lookup, commit to 0.9+."""


class ConfidenceScore(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


_CONFIDENCE_RE = re.compile(r'"?confidence"?\s*[:=]\s*(1(?:\.0+)?|0?\.\d+|0|1)', re.IGNORECASE)


def _parse_confidence_from_text(text: str) -> ConfidenceScore:
    """Lenient salvage for router models that answer in plain text instead of the structured
    output tool - typically wrapping the score in a tool-call envelope like
    {"name": "final_result", "parameters": {"confidence": 0.8, ...}} (llama3.1:8b does this
    intermittently, live-observed 2026-07-11). The number is right there; refusing to read it
    forced a 0.0 fallback that blocked perfectly good calls behind clarifications.
    """
    match = _CONFIDENCE_RE.search(text)
    if match is None:
        raise ModelRetry('Reply with JSON exactly like {"confidence": 0.8, "reasoning": "..."}')
    value = min(max(float(match.group(1)), 0.0), 1.0)
    return ConfidenceScore(confidence=value, reasoning="parsed from unstructured router reply")


class RouterConfidenceChecker:
    """Fast small-model confidence pre-check feeding `ToolRoutingPolicy.decide()`'s `confidence`
    parameter - the role `infra/ollama/models.md` already assigns to the router model.
    """

    def __init__(self, router_model: Model):
        # Two accepted output shapes: the proper structured ConfidenceScore, or free text that
        # _parse_confidence_from_text can salvage a confidence number from. retries=2 gives one
        # extra attempt when neither shape yields a score before score()'s 0.0 catch-all.
        self._agent = Agent(
            router_model,
            output_type=[ConfidenceScore, TextOutput(_parse_confidence_from_text)],
            system_prompt=ROUTER_SYSTEM_PROMPT,
            retries=2,
        )

    # Live probing (2026-07-11, llama3.1:8b) showed single-sample scores swinging +/-0.2 on
    # identical inputs - enough to flip an obviously-right read-only call across the 0.6
    # threshold at random. Median-of-3 keeps one noisy sample (or one outright failure, which
    # scores 0.0) from deciding the outcome, at the price of two extra small-model calls.
    SAMPLES = 3

    async def score(self, user_text: str, server: str, tool: str, arguments: dict) -> float | None:
        """Median confidence across SAMPLES, or None when the router produced no usable score.

        None means "no opinion", and `ToolRoutingPolicy.decide` treats it as such - it acts and
        lets the CDG do the actual gating. It is deliberately NOT 0.0: a fabricated 0.0 is
        indistinguishable from the router judging a call worthless, and that conflation took the
        whole system down silently (see _score_once).
        """
        prompt = (
            f"User message: {user_text!r}\n"
            f"Proposed action: call tool {tool!r} on server {server!r} with arguments {arguments!r}"
        )
        samples = await asyncio.gather(*(self._score_once(prompt, server, tool) for _ in range(self.SAMPLES)))
        scored = [s for s in samples if s is not None]
        if not scored:
            # Every sample failed - this router cannot score at all against this model. Loud,
            # because it means the routing hint is entirely absent and someone should either fix
            # OLLAMA_ROUTER_MODEL or accept running without the hint.
            logger.warning(
                "Router produced no usable confidence for %s.%s (all %d samples failed); "
                "proceeding without the routing hint - the CDG still gates this call. "
                "Check OLLAMA_ROUTER_MODEL: not every model can produce a calibrated score.",
                server,
                tool,
                self.SAMPLES,
            )
            return None
        return statistics.median(scored)

    async def _score_once(self, prompt: str, server: str, tool: str) -> float | None:
        try:
            result = await self._agent.run(prompt)
        except Exception:  # noqa: BLE001 - a broken router must never take the whole turn down
            # Returns None ("no opinion"), NOT 0.0. The 0.0 this used to return was described as
            # "failing toward caution", but routing is explicitly not the safety layer - the CDG
            # is, and it runs regardless. So the caution bought nothing, while the fabricated
            # score was indistinguishable from a real judgement of "worthless call".
            #
            # Measured cost of that conflation (2026-07-15): with the configured
            # OLLAMA_ROUTER_MODEL=qwen2.5:7b-instruct, EVERY sample raised
            # UnexpectedModelBehavior, every score became 0.0, and every tool call in the system
            # turned into NEEDS_CLARIFICATION. Tau could not use a single one of its 77 tools,
            # and nothing reported a fault - the main model just looked stupid, narrating
            # confused prose about "low confidence" instead of acting.
            logger.warning(
                "Router confidence sample failed for %s.%s; this sample has no opinion",
                server,
                tool,
                exc_info=True,
            )
            return None
        return result.output.confidence
