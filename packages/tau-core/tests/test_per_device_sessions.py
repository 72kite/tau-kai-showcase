"""Per-device conversation isolation through TauAssistant.chat (Phase 12, §10.1 #3 / Tier 1 #8).

The bug: one shared SessionManager meant device B's turns were in the model's PROMPT when device
A asked. These tests drive real TauAssistant.chat turns (FunctionModel, no Ollama) and assert on
the exact prompt string the model receives - the one place the leak was observable - so a
regression that re-shares history would fail here, not just in a design doc.
"""

import sys
from pathlib import Path

from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
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
            )
        ]
    )


def _prompt_of(messages) -> str:
    """The user-prompt text the model was handed for this turn (which includes any 'Recent
    conversation' section _render_prompt built from the session history)."""
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    return str(part.content)
    raise AssertionError("no UserPromptPart found")


def recording_model(seen_prompts: list[str]) -> FunctionModel:
    """A main model that never calls a tool - it records the prompt it saw and replies with a
    fixed line, so a turn is a pure "what context did the model get" probe."""

    def respond(messages, info):
        seen_prompts.append(_prompt_of(messages))
        return ModelResponse(parts=[TextPart("ok")])

    return FunctionModel(respond)


async def test_one_devices_history_never_enters_another_devices_prompt():
    seen: list[str] = []
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml"))
        assistant = TauAssistant(
            host, main_model=recording_model(seen), router_model=recording_model([])
        )

        # Device A shares a secret; device B then asks. B's prompt must not contain A's words.
        await assistant.chat("my passphrase is HUNTER2", session_key="deviceA")
        await assistant.chat("what did I just tell you?", session_key="deviceB")

        device_b_prompt = seen[-1]
        assert "HUNTER2" not in device_b_prompt
        assert "passphrase" not in device_b_prompt


async def test_same_device_keeps_its_own_conversation_context():
    seen: list[str] = []
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml"))
        assistant = TauAssistant(
            host, main_model=recording_model(seen), router_model=recording_model([])
        )

        await assistant.chat("my passphrase is HUNTER2", session_key="deviceA")
        await assistant.chat("what did I just tell you?", session_key="deviceA")

        # A device's SECOND turn must carry its OWN first turn forward (continuity within a device
        # is the flip side of isolation across devices).
        assert "HUNTER2" in seen[-1]


async def test_anonymous_turns_share_the_default_session():
    seen: list[str] = []
    registry = build_registry()
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        host = TauCoreHost(manager, settings=TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml"))
        assistant = TauAssistant(
            host, main_model=recording_model(seen), router_model=recording_model([])
        )

        # No session_key (curl, tests, a client sending no X-Tau-Device-Id): these cannot be told
        # apart, so they share the default session - documented, not accidental.
        await assistant.chat("first anonymous message", session_key=None)
        await assistant.chat("second anonymous message", session_key="")

        assert "first anonymous message" in seen[-1]
        # And the shared default session is a distinct object from any per-device one.
        assert assistant._resolve_session(None) is assistant.session
        assert assistant._resolve_session("deviceA") is not assistant.session
