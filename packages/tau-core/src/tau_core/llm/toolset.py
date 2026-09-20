from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from mcp.types import Tool as MCPTool
from pydantic_ai.tools import Tool as AgentTool
from pydantic_ai.toolsets import FunctionToolset

from tau_core.cdg import CoreDirectiveViolation
from tau_core.cdg.rules import Effect
from tau_core.host import TauCoreHost, ToolCallStatus
from tau_core.llm.router import RouterConfidenceChecker
from tau_core.mcp_client import ServerCallError, ServerNotConnectedError

logger = logging.getLogger(__name__)

# Tools the MODEL must never be offered, regardless of what the CDG says.
#
# This can't be a CDG rule. The CDG matches on server/tool/arguments and has no concept of *who*
# is calling, so a deny here would equally block the admin-only HTTP endpoints that read these
# same tools through the identical host.call_tool path - endpoints that already do their own
# _require_admin check. Toolset-build time is the only layer that knows "the caller is the LLM"
# rather than "the caller is an authenticated admin". web/server.py imports this same set for its
# passthrough block (see _TOOLS_BLOCKED_FROM_PASSTHROUGH) - one list, not two, because a Phase 11
# audit found the two had already drifted apart.
MODEL_EXCLUDED_TOOLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("ui-bridge-mcp-server", "get_unified_transcript"),
        ("memory-mcp-server", "list_drafts"),
        # Phase 38: session cookies and captured request/response headers are credentials in
        # every practical sense - putting them in the model's context is a leak into the
        # transcript/memory-recall/audit log, the same reasoning that redacts audio_base64/
        # snapshot_b64/embedding from the audit trail (Phase 7 Tier 0). web/server.py's
        # _TOOLS_BLOCKED_FROM_PASSTHROUGH is derived from this same set, so this is a full block,
        # not just an LLM-only one - the generic /api/tools passthrough refuses both too (403,
        # "use its dedicated tier-gated endpoint"). No dedicated endpoint exists for either as of
        # this phase; if a human genuinely needs to read a captured cookie/header for debugging,
        # that is a deliberate follow-up (a bespoke admin route, same shape as /api/voice/enroll),
        # not something to leave reachable by default in the meantime.
        ("stealth-browser-mcp", "get_cookies"),
        ("stealth-browser-mcp", "export_network_data"),
        # These four are internal service-to-service plumbing - each one's own docstring says
        # "called by X-mcp-server or tau-core" - that was being registered into the model's
        # toolset anyway. They're a near-perfect semantic decoy for home control
        # (update_devices_state reads like exactly what "turn on the kitchen light" needs), and
        # Phase 36's sweep measured them stealing calls at every model size tested. Worse than a
        # wasted call: they mutate the dashboard's device/security/vision counters, so a
        # hallucinated one corrupts displayed state rather than failing silently.
        #
        # update_transcription does have a real internal caller (web/server.py, straight through
        # host.call_tool - a different code path from this toolset, so excluding it here doesn't
        # touch that). All four stay ALLOW in the CDG for the same reason as the two above: a CDG
        # rule can't tell the model apart from a legitimate internal caller.
        ("ui-bridge-mcp-server", "update_transcription"),
        ("ui-bridge-mcp-server", "update_security_state"),
        ("ui-bridge-mcp-server", "update_devices_state"),
        ("ui-bridge-mcp-server", "update_vision_state"),
    }
)

# Servers reachable ONLY through spawn_subagent/spawn_subagents, never in the main turn's own
# toolset - regardless of scope_servers_per_turn or what heuristic_servers_needed matches.
# stealth-browser-mcp alone is ~99 tools, roughly this whole project's registry combined in one
# server, and Phase 22/23 measured that roster size directly hurts tool-selection accuracy (see
# project-tau-plan.md §8.25). Putting it in every turn's default roster would reproduce the exact
# problem those phases fixed. Delegating to it through a sub-agent - still via the CDG, like any
# other call - leaves unrelated turns' tool counts alone. build_toolset enforces this with
# `allow_subagent_only_servers`, True only at the one call site inside _run_subagent_task.
SUBAGENT_ONLY_SERVERS: frozenset[str] = frozenset({"stealth-browser-mcp"})

# Phase 40 "visual answer cards": tools whose result may carry a picture worth showing back to
# the user, not just describing in text - currently just research-mcp-server's search_images.
# Deliberately a small, explicit set (like MODEL_EXCLUDED_TOOLS/OWNER_SCOPED_TOOLS above) rather
# than a heuristic over every tool's output, so adding a new image-capable tool later is a
# one-line, reviewed decision.
IMAGE_RESULT_TOOLS: frozenset[tuple[str, str]] = frozenset(
    {("research-mcp-server", "search_images")}
)


def _extract_first_image(text: str) -> dict[str, str] | None:
    """Pulls the first image out of an IMAGE_RESULT_TOOLS call's rendered text - the SAME text
    the model reads, framed by research_mcp_server.server._frame() as untrusted web content
    (`<<<UNTRUSTED_WEB_CONTENT>>>\\nsource: ...\\n...\\n---\\n{json body}\\n<<<END_...>>>`). There
    is no separate "structured" channel to read instead - an MCP tool's result is this string,
    full stop - so this parses the one known-fixed part of that format (the JSON array between
    the "---" line and the close marker) rather than the untrusted prose around it. Best-effort:
    any parse failure means "no image for this turn", never a crashed turn - the reply still
    works, it just doesn't get a picture attached.
    """
    try:
        _, _, body_and_close = text.partition("\n---\n")
        body, _, _ = body_and_close.rpartition("\n<<<END_UNTRUSTED_WEB_CONTENT>>>")
        results = json.loads(body)
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict) or not first.get("image_url"):
            return None
        return {
            "url": str(first.get("image_url", "")),
            "title": str(first.get("title", "")),
            "source_url": str(first.get("source_url", "")),
            "source": str(first.get("source", "")),
        }
    except (json.JSONDecodeError, TypeError, KeyError, IndexError):
        return None


# Memory tools whose `owner` argument tau-core sets from the voice-identified SPEAKER, server-
# side, overriding anything the model supplies (Phase 13.5, "everything per-speaker"). Owner is
# an identity claim - who a memory belongs to and who may recall it - so letting the model choose
# it would let one turn write into, or read out of, another person's memories. The model never
# gets to assert identity; tau-core stamps it from the same voice ID that tags the transcript.
# For draft/store this binds a new memory to its owner; for search it scopes recall to that
# speaker (their own memories + shared) - see MemoryTreeStore.search.
OWNER_SCOPED_TOOLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("memory-mcp-server", "draft_memory"),
        ("memory-mcp-server", "store_memory"),
        ("memory-mcp-server", "search_memory"),
    }
)

# How the unauthenticated /api/tools passthrough must treat each OWNER_SCOPED_TOOL (Phase 15 #1).
# The stamping above only protects the *model* path; the passthrough forwards arguments verbatim
# and has no verified speaker to scope by, so left alone it lets any client read/write another
# person's memories by supplying `owner`. Split by direction:
#   - writes are blocked outright - there is no honest speaker to attribute a new memory to;
#   - the read (search) stays reachable for the admin MemoryPanel but is forced to shared-only
#     recall (owner="") there, since a caller must never get to choose whose memories to read.
# Their union must equal OWNER_SCOPED_TOOLS so a tool can never be added to one without being
# classified here (asserted below).
PASSTHROUGH_BLOCKED_MEMORY_WRITES: frozenset[tuple[str, str]] = frozenset(
    {
        ("memory-mcp-server", "draft_memory"),
        ("memory-mcp-server", "store_memory"),
    }
)
PASSTHROUGH_SHARED_ONLY_MEMORY_READS: frozenset[tuple[str, str]] = frozenset(
    {
        ("memory-mcp-server", "search_memory"),
    }
)
assert (
    PASSTHROUGH_BLOCKED_MEMORY_WRITES | PASSTHROUGH_SHARED_ONLY_MEMORY_READS
) == OWNER_SCOPED_TOOLS, "every owner-scoped tool must have a passthrough policy"


def qualified_tool_name(server: str, tool_name: str) -> str:
    """Prefixes an MCP tool name with its server so tools from different servers never collide
    in the single namespace the model sees.
    """
    return f"{server}__{tool_name}"


# A tool result the model can read, from whatever content blocks MCP returned. Anything that is
# not text is DESCRIBED rather than inlined.
#
# This used to be `content[0].text`, which had two problems that only showed up when a server
# returning something other than text was first added (openscad-mcp-server's render_scad_png,
# Phase 25):
#
#   1. `ImageContent` has `.data`/`.mimeType` and no `.text` at all, so `content[0].text` raised
#      AttributeError - and `call()` below catches only CDG/transport errors, so it propagated out
#      through PydanticAI and killed the entire turn. Every server in the repo happened to return
#      text, so nothing had ever exercised the path.
#   2. Even with the crash fixed, inlining the payload would be wrong: the local model is
#      text-only, so several thousand characters of base64 PNG costs a large share of the context
#      window to convey nothing it can perceive. A one-line description is strictly more useful.
#
# Multi-block results are joined rather than truncated to the first, matching the fix
# web/server.py already made for list-returning tools.
_MAX_TOOL_RESULT_CHARS = 8_000


def _content_to_text(content: list) -> str:
    if not content:
        return "(tool returned no content)"
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
            continue
        data = getattr(block, "data", None)
        if data is not None:
            mime = getattr(block, "mimeType", None) or "application/octet-stream"
            parts.append(
                f"({mime} returned, {len(data)} base64 chars - binary content is not shown to you. "
                "Describe what you generated and tell the user it is available; do not attempt to "
                "read or reproduce the bytes.)"
            )
            continue
        resource = getattr(block, "resource", None)
        if resource is not None:
            uri = getattr(resource, "uri", "(unknown uri)")
            mime = getattr(resource, "mimeType", None) or "unknown type"
            # An embedded resource may itself carry text (e.g. an STL is ASCII); prefer it when
            # small enough to be worth reading, and describe it otherwise.
            r_text = getattr(resource, "text", None)
            if r_text is not None and len(r_text) <= _MAX_TOOL_RESULT_CHARS:
                parts.append(f"(resource {uri}, {mime}):\n{r_text}")
            else:
                size = len(r_text) if r_text is not None else len(getattr(resource, "blob", "") or "")
                parts.append(
                    f"(resource {uri} of type {mime}, {size} chars - saved, not shown to you.)"
                )
            continue
        parts.append(f"({type(block).__name__} content block returned)")

    joined = "\n".join(parts)
    if len(joined) > _MAX_TOOL_RESULT_CHARS:
        joined = joined[:_MAX_TOOL_RESULT_CHARS] + f"\n...[truncated, {len(joined)} chars total]"
    return joined


def _make_wrapped_tool(
    host: TauCoreHost,
    checker: RouterConfidenceChecker,
    user_text: str,
    server: str,
    tool: MCPTool,
    pending_approval_ids: list[str],
    speaker: str = "",
    image_results: list[dict[str, str]] | None = None,
) -> AgentTool:
    """Wraps one MCP tool as a PydanticAI tool that always routes through `TauCoreHost.call_tool` -
    never a direct MCP call - so the CDG, approval queue, and audit log stay in the loop no matter
    what the LLM decides.

    `speaker` is the voice-identified person for this turn; for OWNER_SCOPED_TOOLS it is stamped
    into the `owner` argument server-side, overriding any model-supplied value, so memory identity
    is never something the model gets to assert.

    `image_results` (Phase 40) collects a picture from any IMAGE_RESULT_TOOLS call this turn makes,
    same append-to-a-list-passed-by-reference pattern as `pending_approval_ids` - the caller reads
    it back after the agent run to attach an image to AssistantTurn.
    """
    owner_scoped = (server, tool.name) in OWNER_SCOPED_TOOLS
    image_producing = (server, tool.name) in IMAGE_RESULT_TOOLS

    async def call(**kwargs: Any) -> str:
        if owner_scoped:
            # Overwrite, don't default: the model must not be able to write to or read from
            # another speaker's memories by supplying its own `owner`.
            kwargs = {**kwargs, "owner": speaker}

        # CDG-tier router bypass: the router is a confidence *hint* feeding ToolRoutingPolicy,
        # never the safety layer - the CDG (below, via host.call_tool) gates every call
        # regardless of what the router says. So for a tool the CDG ruleset already trusts to
        # run unattended (Effect.ALLOW) and that isn't pinned to always-confirm, scoring it costs
        # 3 concurrent local-model calls to buy a hint nobody downstream needs. Skipping ties the
        # outcome to what ToolRoutingPolicy.decide already does with confidence=None: proceed,
        # CDG still gates it. REQUIRE_APPROVAL/DENY-tier tools still get scored, since a
        # confidence-gated clarify is worth the cost exactly where a wrong call isn't free.
        if host.cdg.evaluate(server, tool.name).effect is Effect.ALLOW and not host.routing.always_clarifies(
            tool.name
        ):
            confidence = None
        else:
            confidence = await checker.score(user_text, server, tool.name, kwargs)
        try:
            outcome = await host.call_tool(server, tool.name, kwargs, confidence=confidence)
        except CoreDirectiveViolation as exc:
            return f"DENIED: {exc.reason}"
        except (ServerCallError, ServerNotConnectedError) as exc:
            # Isolating build_toolset is not enough on its own: a server can die (or a call can
            # exceed the MCP call timeout) between building this turn's toolset and invoking it,
            # and an exception raised here propagates out through PydanticAI and takes the whole
            # turn down with a 502 - the very failure Phase 7 Tier 0 exists to prevent. Report it
            # to the model as a tool outcome instead, in the same shape as DENIED /
            # NEEDS_CLARIFICATION / PENDING_APPROVAL, so it tells the user what broke rather than
            # the turn dying or - worse - the model claiming the action succeeded.
            logger.warning("Tool %s.%s unavailable: %s", server, tool.name, exc)
            return f"UNAVAILABLE: {exc}"

        if outcome.status is ToolCallStatus.CLARIFY:
            return f"NEEDS_CLARIFICATION: {outcome.reason}"
        if outcome.status is ToolCallStatus.PENDING_APPROVAL:
            if outcome.approval_request_id:
                pending_approval_ids.append(outcome.approval_request_id)
            return f"PENDING_APPROVAL(id={outcome.approval_request_id}): {outcome.reason}"

        content = outcome.result.content if outcome.result else []
        text = _content_to_text(content)
        if image_producing and image_results is not None:
            image = _extract_first_image(text)
            if image:
                image_results.append(image)
        return text

    return AgentTool.from_schema(
        function=call,
        name=qualified_tool_name(server, tool.name),
        description=tool.description or f"Call {tool.name} on the {server} MCP server.",
        json_schema=tool.inputSchema,
        takes_ctx=False,
    )


async def build_toolset(
    host: TauCoreHost,
    checker: RouterConfidenceChecker,
    user_text: str,
    pending_approval_ids: list[str],
    servers: list[str] | None = None,
    speaker: str = "",
    allow_subagent_only_servers: bool = False,
    image_results: list[dict[str, str]] | None = None,
) -> FunctionToolset:
    """Builds a fresh toolset scoped to one turn, so the router's confidence check always sees
    the current `user_text` rather than a stale closure from a previous message. Any tool call
    that lands in PENDING_APPROVAL during this turn appends its request id to
    `pending_approval_ids`, which the caller passes in and reads back after the run.
    `image_results` (Phase 40), if given, collects a picture from any IMAGE_RESULT_TOOLS call -
    same passed-by-reference pattern.

    `servers` restricts the toolset to those MCP servers (used to scope sub-agents to the
    domains their task actually needs). Unknown names raise ValueError so a caller passing a
    typo'd server name finds out immediately rather than silently getting no tools from it.

    Candidates are the *registered* servers, not the currently-live ones (Phase 7 Tier 0). Two
    reasons: `list_tools` reconnects a dead server on demand, so filtering to live sessions here
    would mean a server that dropped once never came back without restarting tau-core; and an
    unknown-server ValueError should mean "you typo'd the name", not "that server happens to be
    down this second" - the latter is a transient the sub-agent shouldn't be told is a typo.

    `allow_subagent_only_servers` (Phase 38) must stay False for the main turn's own toolset -
    SUBAGENT_ONLY_SERVERS are stripped from `candidates` regardless of what `servers` contains,
    so neither an unscoped turn (every server) nor a heuristic match that happens to include one
    can leak it into the main model's roster. `_run_subagent_task` is the one call site that
    passes True, since a sub-agent being explicitly delegated to that server is the intended way
    to reach it - the CDG still gates every individual call either way.
    """
    candidates = host.mcp.registered_servers()
    if servers is not None:
        unknown = sorted(set(servers) - set(candidates))
        if unknown:
            raise ValueError(
                f"Unknown MCP server(s) {unknown}; registered servers are {sorted(candidates)}"
            )
        candidates = [name for name in candidates if name in servers]
    if not allow_subagent_only_servers:
        candidates = [name for name in candidates if name not in SUBAGENT_ONLY_SERVERS]

    # Per-server isolation: one server failing to list its tools must cost only that server's
    # tools this turn, not the turn. Before this, a single zombie session raising here 502'd
    # every chat - including questions that needed no tools at all. Gathered rather than looped
    # so a slow/reconnecting server doesn't serialise its timeout in front of healthy ones.
    results = await asyncio.gather(
        *(host.mcp.list_tools(server) for server in candidates), return_exceptions=True
    )

    toolset: FunctionToolset = FunctionToolset()
    for server, tools in zip(candidates, results):
        if isinstance(tools, BaseException):
            logger.warning("Skipping MCP server %r this turn: list_tools failed: %r", server, tools)
            continue
        for tool in tools:
            if (server, tool.name) in MODEL_EXCLUDED_TOOLS:
                continue
            toolset.add_tool(
                _make_wrapped_tool(
                    host, checker, user_text, server, tool, pending_approval_ids, speaker,
                    image_results=image_results,
                )
            )
    return toolset
