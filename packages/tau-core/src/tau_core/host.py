from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from mcp.types import CallToolResult

from tau_core import crypto_store
from tau_core.approval import PendingActionQueue
from tau_core.cdg import ApprovalRequiredError, CoreDirectiveGuard, CoreDirectiveViolation, load_rules
from tau_core.cdg.rules import Effect
from tau_core.config import TauCoreSettings
from tau_core.logging_setup import log_tool_call
from tau_core.mcp_client import MCPClientManager, load_server_registry
from tau_core.routing import RoutingAction, ToolRoutingPolicy
from tau_core.session import MemoryTreeBackend, SessionManager
from tau_core.session import store as session_store


class ToolCallStatus(str, Enum):
    EXECUTED = "executed"
    CLARIFY = "clarify"
    PENDING_APPROVAL = "pending_approval"


@dataclass(frozen=True)
class ToolCallOutcome:
    status: ToolCallStatus
    reason: str
    result: CallToolResult | None = None
    approval_request_id: str | None = None


class TauCoreHost:
    """The MCP Host: wires the Core Directive Guard, MCP client manager, session manager,
    approval queue, and routing policy into the single pipeline every tool call must pass
    through (Phase 1 of the build plan).

    Pipeline for every call: routing policy (act vs. clarify) -> CDG (allow/deny/require
    approval) -> MCP transport -> audit log. The CDG cannot be bypassed by any code path in
    this class - `call_tool` is the only way to reach `self.mcp`.
    """

    def __init__(
        self,
        mcp_manager: MCPClientManager,
        settings: TauCoreSettings | None = None,
        session: SessionManager | None = None,
        approval_queue: PendingActionQueue | None = None,
        routing_policy: ToolRoutingPolicy | None = None,
    ):
        self.settings = settings or TauCoreSettings()
        self.cdg = CoreDirectiveGuard(load_rules(self.settings.cdg_rules_path))
        self.mcp = mcp_manager
        # Default session recalls from memory-mcp-server's Memory Tree (Phase 2.8's "recall
        # relevant context at the start of a turn"); MemoryTreeBackend degrades to no-recall
        # whenever that server isn't connected, so echo/dangerous-only setups are unaffected.
        # Durable by default (Phase 10.3 #11, session persistence), same reasoning as the
        # approval queue below: a restart used to silently drop every in-progress conversation.
        session_store_path = self.settings.session_store_path
        session_key = (
            crypto_store.resolve_key(session_store_path) if session_store_path is not None else None
        )

        def _persist_default_session(mgr: SessionManager) -> None:
            session_store.save_session_history(session_store_path, session_key, mgr.history())

        self.session = session or SessionManager(
            max_messages=self.settings.session_max_messages,
            memory_backend=MemoryTreeBackend(self),
            initial_messages=session_store.load_session_history(session_store_path, session_key),
            on_change=_persist_default_session,
        )
        # Volume-backed by default (TAU_APPROVAL_STORE_PATH), so a tau-core restart no longer
        # silently drops every pending human decision - Phase 7 Tier 0 item 5.
        # Encrypted at rest (Phase 10) when TAU_MASTER_KEY is set - the queue carries raw
        # voiceprint embeddings (see web/server.py's voice_enroll). Plaintext, unchanged, if unset.
        encryption_key = (
            crypto_store.resolve_key(self.settings.approval_store_path)
            if self.settings.approval_store_path is not None
            else None
        )
        self.approvals = approval_queue or PendingActionQueue(
            store_path=self.settings.approval_store_path,
            encryption_key=encryption_key,
        )
        self.routing = routing_policy or ToolRoutingPolicy(
            confidence_threshold=self.settings.routing_confidence_threshold
        )

    @classmethod
    def from_settings(cls, settings: TauCoreSettings | None = None) -> "TauCoreHost":
        settings = settings or TauCoreSettings()
        registry = load_server_registry(settings.servers_config_path)
        manager = MCPClientManager(
            registry,
            call_timeout=settings.mcp_call_timeout_seconds,
            connect_timeout=settings.mcp_connect_timeout_seconds,
        )
        return cls(manager, settings=settings)

    async def call_tool(
        self,
        server: str,
        tool: str,
        arguments: dict[str, Any],
        *,
        # None = the router had no usable opinion (not "zero confidence"); the routing policy
        # then defers to the CDG rather than vetoing. See ToolRoutingPolicy.decide.
        confidence: float | None = 1.0,
        approval_request_id: str | None = None,
        requested_by: str = "tau-core",
    ) -> ToolCallOutcome:
        routing_decision = self.routing.decide(tool, confidence)
        if routing_decision.action is RoutingAction.CLARIFY:
            # Clarifications must be auditable too: a call Tau declined to make is as much a
            # part of "what happened" as one it made (this path was silently unlogged until
            # 2026-07-11, which made the activity feed - and the stderr trail - lie by omission).
            log_tool_call(
                server=server, tool=tool, arguments=arguments,
                effect="routing_clarify", outcome="clarify", reason=routing_decision.reason,
            )
            return ToolCallOutcome(status=ToolCallStatus.CLARIFY, reason=routing_decision.reason)

        approval = self.approvals.to_approved_action(approval_request_id) if approval_request_id else None

        try:
            decision = self.cdg.enforce(server, tool, arguments, approval=approval)
        except ApprovalRequiredError as exc:
            request = self.approvals.submit(
                server=server, tool=tool, arguments=arguments, reason=exc.reason, requested_by=requested_by
            )
            log_tool_call(
                server=server, tool=tool, arguments=arguments,
                effect="require_approval", outcome="pending", reason=exc.reason,
            )
            return ToolCallOutcome(
                status=ToolCallStatus.PENDING_APPROVAL,
                reason=exc.reason,
                approval_request_id=request.id,
            )
        except CoreDirectiveViolation as exc:
            log_tool_call(
                server=server, tool=tool, arguments=arguments,
                effect="deny", outcome="denied", reason=exc.reason,
            )
            raise

        # The approval got us past a REQUIRE_APPROVAL rule, so spend it - one human sign-off
        # authorises exactly one execution (see PendingActionQueue.redeem). Deliberately narrow:
        # only when an approval was supplied AND the rule that matched actually required one, so
        # passing an id to a call the CDG would have allowed anyway doesn't silently burn it.
        # Redeeming here rather than after `mcp.call_tool` keeps the window closed - a call that
        # fails in transport must not leave a live approval behind for someone to replay.
        if approval is not None and decision.effect is Effect.REQUIRE_APPROVAL:
            self.approvals.redeem(approval.request_id)

        result = await self.mcp.call_tool(server, tool, arguments)
        log_tool_call(
            server=server, tool=tool, arguments=arguments,
            effect=decision.effect.value, outcome="executed", reason=decision.reason,
        )
        return ToolCallOutcome(status=ToolCallStatus.EXECUTED, reason=decision.reason, result=result)
