from __future__ import annotations

from dataclasses import dataclass

from tau_core.cdg.exceptions import ApprovalRequiredError, CoreDirectiveViolation
from tau_core.cdg.rules import Effect, RuleSet
from tau_core.hashing import hash_arguments


@dataclass(frozen=True)
class Decision:
    effect: Effect
    rule_id: str | None
    reason: str


@dataclass(frozen=True)
class ApprovedAction:
    """Proof that a human approved this exact tool call. Constructed from an approved ActionRequest, never by the LLM."""

    request_id: str
    server: str
    tool: str
    arguments_hash: str
    approved_by: str


class CoreDirectiveGuard:
    """Deterministic, LLM-unreachable policy layer. Every tool call must pass through enforce() before execution.

    This class holds no LLM state and takes no free-text instructions - it only ever consults the loaded
    ruleset. The ruleset file is intentionally outside the self-upgrade pipeline's write path (see Phase 4
    of the build plan): nothing in the governed-upgrade flow may edit it.
    """

    def __init__(self, ruleset: RuleSet):
        self._ruleset = ruleset

    def evaluate(self, server: str, tool: str) -> Decision:
        for rule in self._ruleset.rules:
            if rule.matches(server, tool):
                return Decision(effect=rule.effect, rule_id=rule.id, reason=rule.reason)
        return Decision(
            effect=self._ruleset.default_effect,
            rule_id=None,
            reason="no matching rule; default effect applied",
        )

    def enforce(
        self,
        server: str,
        tool: str,
        arguments: dict,
        approval: ApprovedAction | None = None,
    ) -> Decision:
        decision = self.evaluate(server, tool)
        call_label = f"{server}.{tool}"

        if decision.effect is Effect.DENY:
            raise CoreDirectiveViolation(call_label, decision.reason, decision.rule_id)

        if decision.effect is Effect.REQUIRE_APPROVAL:
            if not self._approval_satisfies(approval, server, tool, arguments):
                raise ApprovalRequiredError(
                    call_label,
                    decision.reason,
                    decision.rule_id,
                    request_id=approval.request_id if approval else None,
                )

        return decision

    @staticmethod
    def _approval_satisfies(
        approval: ApprovedAction | None, server: str, tool: str, arguments: dict
    ) -> bool:
        if approval is None:
            return False
        return (
            approval.server == server
            and approval.tool == tool
            and approval.arguments_hash == hash_arguments(arguments)
        )
