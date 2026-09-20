"""Generic tool-call passthrough and MCP Resource reads for frontend-initiated actions.

Split out of web/server.py's create_app() in Phase 49. `_TOOLS_BLOCKED_FROM_PASSTHROUGH` is
re-exported from `tau_core.web.server` (see that module) - `test_toolset.py` imports it from
there directly, and that import path is a public contract this refactor must not break.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from tau_core.approval.queue import ApprovalError
from tau_core.cdg import CoreDirectiveViolation
from tau_core.host import ToolCallStatus
from tau_core.llm.toolset import (
    MODEL_EXCLUDED_TOOLS,
    PASSTHROUGH_BLOCKED_MEMORY_WRITES,
    PASSTHROUGH_SHARED_ONLY_MEMORY_READS,
)
from tau_core.mcp_client import ServerCallError, ServerNotConnectedError
from tau_core.web.deps import SharedDeps, _device_id, _sanitized_error

# Tools the generic /api/tools passthrough must refuse. Two different reasons, unioned:
#
# - MODEL_EXCLUDED_TOOLS: sensitive reads with their own tier-gated endpoints, so a passthrough
#   call would sidestep the admin gate. Imported rather than re-listed - a Phase 11 audit found
#   these two lists had already drifted apart once.
# - PASSTHROUGH_BLOCKED_MEMORY_WRITES: draft_memory/store_memory stay in the *model's* toolset,
#   because that path stamps `owner` from the voice-identified speaker server-side. The
#   passthrough forwards `owner` verbatim with no verified identity, so open access there let any
#   client write a memory attributed to anyone. search_memory is handled below instead (forced to
#   shared-only) because the admin MemoryPanel genuinely needs it.
_TOOLS_BLOCKED_FROM_PASSTHROUGH = MODEL_EXCLUDED_TOOLS | PASSTHROUGH_BLOCKED_MEMORY_WRITES


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}
    requested_by: str = "tablet-ui"
    # Id of an approval a human already granted for THIS exact call, to re-issue it now that it
    # is authorised (Phase 8.C). Approving does not execute anything - it only records consent -
    # so an approval-gated tool invoked over this endpoint previously had no way to complete at
    # all: the caller got `pending_approval` and there was no parameter to come back with. That
    # is why voice enrolment needed bespoke two-stage endpoints.
    #
    # Passing an arbitrary id here grants nothing: CoreDirectiveGuard._approval_satisfies binds
    # an approval to the exact server/tool/arguments-hash it was granted for, so an approval for
    # one action cannot authorise another.
    approval_request_id: str | None = None


def build_tools_router(deps: SharedDeps) -> APIRouter:
    router = APIRouter()
    host = deps.host

    @router.get("/api/resources/{server}/{uri:path}")
    async def get_resource(server: str, uri: str) -> dict:
        """Fetch one MCP Resource, e.g. GET /api/resources/ui-bridge-mcp-server/ui://state.

        Returns the resource's JSON body directly (not wrapped) so the frontend can treat this
        endpoint as a drop-in replacement for a native MCP Resource subscription.
        """
        try:
            result = await host.mcp.read_resource(server, uri)
        except Exception as exc:  # noqa: BLE001 - sanitized (Phase 7 Tier 1 #9); logged below
            raise _sanitized_error(
                exc, 502, f"Resource '{uri}' on '{server}' is unavailable.", "get_resource"
            ) from exc
        if not result.contents:
            raise HTTPException(status_code=404, detail="Resource returned no content")
        text = result.contents[0].text
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    @router.post("/api/tools/{server}/{tool}")
    async def call_tool(
        server: str,
        tool: str,
        body: ToolCallRequest,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Generic tool-call passthrough for frontend-initiated actions (e.g. a manual
        "refresh approvals" button, or approving/re-triggering a design request). Still goes
        through TauCoreHost.call_tool, so CDG rules apply identically to the LLM path.
        """
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        if (server, tool) in _TOOLS_BLOCKED_FROM_PASSTHROUGH:
            raise HTTPException(
                status_code=403,
                detail=f"{server}.{tool} is not callable here; use its dedicated tier-gated endpoint.",
            )
        arguments = body.arguments
        if (server, tool) in PASSTHROUGH_SHARED_ONLY_MEMORY_READS:
            # The passthrough has no verified speaker identity, so it must never let a caller
            # choose whose memories to read (Phase 15 #1). Force shared-only recall, overriding
            # any supplied `owner` - this is what the admin MemoryPanel already relies on.
            arguments = {**arguments, "owner": ""}
        try:
            outcome = await host.call_tool(
                server,
                tool,
                arguments,
                requested_by=body.requested_by,
                approval_request_id=body.approval_request_id,
            )
        except ApprovalError as exc:
            # Unknown/expired/not-yet-approved id. 409 rather than 500: the caller's request was
            # well-formed, the approval just isn't usable.
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CoreDirectiveViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        except ServerNotConnectedError as exc:
            # That domain server is down (or backing off after failing to come back). This
            # endpoint used to let transport errors escape as a bare 500 "Internal Server
            # Error", which tells a caller nothing about whether to retry - found by killing a
            # domain server under the running bridge (Phase 7 Tier 0). Message sanitized (Phase
            # 7 Tier 1 #9): this endpoint has no auth, and the caller already knows server/tool.
            raise _sanitized_error(
                exc, 503, f"{server} is currently unavailable.", f"call_tool {server}.{tool}"
            ) from exc
        except ServerCallError as exc:
            raise _sanitized_error(
                exc, 502, f"{server}.{tool} call failed.", f"call_tool {server}.{tool}"
            ) from exc

        if outcome.status is ToolCallStatus.EXECUTED:
            content = outcome.result.content if outcome.result else []
            texts = [item.text for item in content if getattr(item, "text", None)]
            return {
                "status": outcome.status.value,
                "reason": outcome.reason,
                # `result` keeps the original single-text shape for existing callers;
                # `results` carries every content block - FastMCP tools returning a list
                # (e.g. memory-mcp-server.search_memory) produce one block per item, and
                # only surfacing the first silently dropped the rest.
                "result": texts[0] if texts else None,
                "results": texts,
            }
        return {
            "status": outcome.status.value,
            "reason": outcome.reason,
            "approval_request_id": outcome.approval_request_id,
        }

    return router
