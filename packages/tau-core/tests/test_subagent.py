"""Proves spawn_subagent is not a way around the Phase 1 enforcement pipeline: a sub-agent's
tool calls flow through the same host.call_tool wrappers as the main agent's, its toolset is
scoped to the servers the parent granted, and it cannot recurse (its toolset never contains
spawn_subagent). FunctionModels throughout - fully offline, no real Ollama.
"""

import sys
from pathlib import Path

from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from tau_core.approval import ApprovalStatus
from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.llm.toolset import qualified_tool_name
from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def build_registry() -> ServerRegistry:
    return ServerRegistry(
        servers=[
            ServerConfig(
                name="echo",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "echo_mcp_server.py")],
            ),
            ServerConfig(
                name="dangerous",
                transport="stdio",
                command=sys.executable,
                args=[str(EXAMPLES_DIR / "dangerous_mcp_server.py")],
            ),
        ]
    )


def build_settings() -> TauCoreSettings:
    return TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml")


def stub_router_model(confidence: float) -> FunctionModel:
    def respond(messages, info):
        return ModelResponse(
            parts=[ToolCallPart(tool_name="final_result", args={"confidence": confidence, "reasoning": "stub"})]
        )

    return FunctionModel(respond)


def _tool_return_text(messages) -> str:
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, ToolReturnPart):
                    return str(part.content)
    raise AssertionError("no ToolReturnPart found in message history")


def spawning_main_model(task: str, servers: list[str]) -> FunctionModel:
    """Main model that delegates to a sub-agent on the first request, then relays its report."""

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="spawn_subagent", args={"task": task, "servers": servers})]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    return FunctionModel(main_fn)


async def test_subagent_executes_scoped_task():
    def sub_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": "from sub"})]
            )
        return ModelResponse(parts=[TextPart(f"sub-agent report: {_tool_return_text(messages)}")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(
            host,
            main_model=spawning_main_model("say 'from sub' via echo", ["echo"]),
            router_model=stub_router_model(0.95),
            subagent_model=FunctionModel(sub_fn),
        )

        turn = await assistant.chat("have someone echo for me")

        assert turn.pending_approval_ids == []
        assert "sub-agent report: from sub" in turn.reply


async def test_subagent_cannot_bypass_cdg():
    """A sub-agent calling shutdown_host must land in PENDING_APPROVAL exactly like any other
    caller, and the approval id must surface on the parent turn."""

    def sub_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name=qualified_tool_name("dangerous", "shutdown_host"), args={"vmid": 101}
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(f"sub-agent report: {_tool_return_text(messages)}")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(
            host,
            main_model=spawning_main_model("shut down vm 101", ["dangerous"]),
            router_model=stub_router_model(0.95),
            subagent_model=FunctionModel(sub_fn),
        )

        turn = await assistant.chat("delegate: shut down vm 101")

        assert len(turn.pending_approval_ids) == 1
        pending = host.approvals.get(turn.pending_approval_ids[0])
        assert pending.status is ApprovalStatus.PENDING
        assert "PENDING_APPROVAL" in turn.reply


async def test_subagent_toolset_is_scoped_and_cannot_recurse():
    """servers=["echo"] must hide every dangerous.* tool from the sub-agent, and no sub-agent
    ever sees spawn_subagent itself - depth-1 by construction, not by convention."""
    sub_tool_names: list[str] = []

    def sub_fn(messages, info):
        sub_tool_names.extend(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart("scoped report")])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(
            host,
            main_model=spawning_main_model("inspect your tools", ["echo"]),
            router_model=stub_router_model(0.95),
            subagent_model=FunctionModel(sub_fn),
        )

        turn = await assistant.chat("delegate a scoped task")

        assert "scoped report" in turn.reply
        assert qualified_tool_name("echo", "echo") in sub_tool_names
        assert not any(name.startswith("dangerous__") for name in sub_tool_names)
        assert "spawn_subagent" not in sub_tool_names


async def test_spawn_subagents_runs_independent_tasks_concurrently():
    """spawn_subagents (plural) fans out N tasks to N sub-agents and returns their reports in
    the same order as the input list, regardless of completion order."""
    import asyncio

    started = []
    release = {"first": asyncio.Event(), "second": asyncio.Event()}

    def _user_prompt_text(messages) -> str:
        # The first ModelRequest bundles the sub-agent's system prompt AND the task text as
        # separate parts - find the UserPromptPart specifically rather than assuming index 0
        # (that's the system prompt, not the task).
        for part in messages[0].parts:
            if isinstance(part, UserPromptPart):
                return str(part.content)
        raise AssertionError("no UserPromptPart found in first message")

    async def sub_fn(messages, info):
        if len(messages) != 1:
            return ModelResponse(parts=[TextPart(f"report: {_tool_return_text(messages)}")])
        text = _user_prompt_text(messages)
        # "first" deliberately waits on "second" starting, to prove they overlap rather than
        # running one after another - if this were sequential, "second" would never start
        # until "first" already returned, and this would deadlock/hang instead of completing.
        if "first" in text:
            started.append("first")
            release["second"].set()
            await asyncio.wait_for(release["first"].wait(), timeout=5)
        else:
            started.append("second")
            release["first"].set()
            await asyncio.wait_for(release["second"].wait(), timeout=5)
        return ModelResponse(
            parts=[ToolCallPart(tool_name=qualified_tool_name("echo", "echo"), args={"text": text})]
        )

    def main_fn(messages, info):
        if len(messages) == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="spawn_subagents",
                        args={
                            "tasks": [
                                {"task": "first task", "servers": ["echo"]},
                                {"task": "second task", "servers": ["echo"]},
                            ]
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart(_tool_return_text(messages))])

    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(
            host,
            main_model=FunctionModel(main_fn),
            router_model=stub_router_model(0.95),
            subagent_model=FunctionModel(sub_fn),
        )

        turn = await assistant.chat("delegate two independent checks")

        assert started == ["first", "second"] or started == ["second", "first"]
        assert "first task" in turn.reply
        assert "second task" in turn.reply


async def test_subagent_unknown_server_reports_instead_of_crashing():
    """A typo'd server name comes back to the main model as SUBAGENT_NOT_STARTED text listing
    the valid names, so it can correct itself - the turn must not die."""
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=build_settings())
        assistant = TauAssistant(
            host,
            main_model=spawning_main_model("anything", ["no-such-server"]),
            router_model=stub_router_model(0.95),
            subagent_model=FunctionModel(lambda m, i: ModelResponse(parts=[TextPart("unused")])),
        )

        turn = await assistant.chat("delegate to a bad server")

        assert "SUBAGENT_NOT_STARTED" in turn.reply
        assert "no-such-server" in turn.reply
