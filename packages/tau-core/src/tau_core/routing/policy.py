from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class RoutingAction(str, Enum):
    ACT = "act"
    CLARIFY = "clarify"


@dataclass(frozen=True)
class RoutingDecision:
    action: RoutingAction
    reason: str


class ToolRoutingPolicy:
    """Decides whether Tau should just call a proposed tool or ask the user a clarifying
    question first, per the build plan's "able to ask for input while helping" requirement.

    This is a routing hint only - it runs *before* the CDG, not instead of it. A low-confidence
    call that would clarify still has to clear the CDG afterwards if the user confirms it.
    """

    def __init__(self, confidence_threshold: float = 0.6, always_clarify_tools: set[str] | None = None):
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        self._threshold = confidence_threshold
        self._always_clarify_tools = always_clarify_tools or set()

    def always_clarifies(self, tool_name: str) -> bool:
        """Whether this tool is configured to always confirm, independent of any confidence
        score. Exposed so callers can decide whether skipping the router entirely (e.g. for a
        CDG-trusted tool) is safe - see RouterConfidenceChecker's CDG-tier bypass.
        """
        return tool_name in self._always_clarify_tools

    def decide(self, tool_name: str, confidence: float | None) -> RoutingDecision:
        """`confidence=None` means the router had NO OPINION - not that it judged the call badly.

        Those are different facts and they get different answers (Phase 9, after the 2026-07-15
        eval). A router that scores a call 0.1 has made a judgement, and clarifying respects it.
        A router that could not produce a score at all has told us nothing, and treating "I don't
        know" as "definitely not" is what silently paralysed the whole system: with
        OLLAMA_ROUTER_MODEL=qwen2.5:7b-instruct every scoring attempt raised, every sample fell
        back to 0.0, and every single tool call in Tau became NEEDS_CLARIFICATION - a total
        outage that no log, health check or test reported.

        Acting on no opinion is safe *because this was never the safety layer*: the CDG runs
        immediately after and gates every dangerous call regardless (see the class docstring and
        TauCoreSettings.routing_confidence_threshold). Failing this hint closed bought nothing
        and cost everything.
        """
        if tool_name in self._always_clarify_tools:
            return RoutingDecision(
                action=RoutingAction.CLARIFY,
                reason=f"'{tool_name}' is configured to always confirm with the user before acting",
            )
        if confidence is None:
            return RoutingDecision(
                action=RoutingAction.ACT,
                reason="router gave no usable confidence; proceeding - the CDG still gates this call",
            )
        if confidence < self._threshold:
            return RoutingDecision(
                action=RoutingAction.CLARIFY,
                reason=f"confidence {confidence:.2f} below threshold {self._threshold:.2f}",
            )
        return RoutingDecision(action=RoutingAction.ACT, reason=f"confidence {confidence:.2f} meets threshold")
