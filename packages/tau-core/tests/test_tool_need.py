"""The Phase 18 tool-need gate. The heuristic is a pure function (exhaustively table-tested); the
classifier is driven by a FunctionModel so it runs offline. The overriding property under test is
the SAFETY CONTRACT: nothing that touches live home/system/time/web/memory state is ever allowed
onto the tool-free fast path, and the classifier fails safe (tools on) on any bad output.
"""

import pytest
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from tau_core.llm.tool_need import ToolNeedClassifier, heuristic_tool_need


# --- Heuristic: things that MUST force the full toolset path -----------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "turn on the kitchen lights",
        "is the front door locked?",
        "set the thermostat to 70",
        "what's the temperature in here",
        "show me the driveway camera",
        "who's at the door",
        "are we in lockdown",
        "is the house secure",
        "how's the 3d print going",
        "launch the drone for a patrol",
        "reboot the media VM",
        "any new CVEs on the cluster",
        "what time is it",
        "what's today's date",
        "what can you do",
        "what model are you running on",
        "what's the weather tomorrow",
        "search the web for the RTX 5090 price",
        "what's the latest news",
        "remember that I like my coffee black",
        "note that the garage code is 1234",
        "don't forget to check the printer later",
        "good morning, what's the temperature?",  # trigger beats the greeting opener
    ],
)
def test_heuristic_flags_stateful_turns_as_tools(text):
    assert heuristic_tool_need(text) == "tools", text


# --- Heuristic: obviously tool-free openers answered with no model call -------------------------
@pytest.mark.parametrize(
    "text",
    [
        "what's 2 + 2",
        "calculate 12 * (3 + 4)",
        "how much is 100 / 7",
        "hi there",
        "hello",
        "good evening",
        "thanks!",
        "thank you so much",
        "how are you",
        "tell me a joke",
    ],
)
def test_heuristic_fast_paths_math_and_smalltalk(text):
    assert heuristic_tool_need(text) == "no_tools", text


# --- Heuristic: everything else is deferred to the classifier -----------------------------------
@pytest.mark.parametrize(
    "text",
    [
        "what's the capital of France",
        "explain how photosynthesis works",
        "why is the sky blue",
        "give me a recipe for banana bread",
        "what does 'ephemeral' mean",
        "how do I tie a bowline knot",
        "what is 15% of 240",  # natural-language 'of' -> not pure-symbolic, so the classifier decides
    ],
)
def test_heuristic_defers_general_knowledge_to_classifier(text):
    assert heuristic_tool_need(text) == "uncertain", text


def _router(reply: str) -> FunctionModel:
    return FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart(reply)]))


async def test_classifier_direct_takes_fast_path():
    clf = ToolNeedClassifier(_router("DIRECT"))
    assert await clf.needs_tools("what's the capital of France") is False


async def test_classifier_tools_keeps_full_path():
    clf = ToolNeedClassifier(_router("TOOLS"))
    assert await clf.needs_tools("is the boiler on") is True


@pytest.mark.parametrize("reply", ["", "  ", "maybe?", "I think DIRECT", "no tools needed"])
async def test_classifier_defaults_to_tools_on_unclear_output(reply):
    """Only a clear, LEADING 'direct' diverts to the fast path; ambiguous or rambling output must
    fail safe to the full path (a false tool-free hallucinates live state)."""
    clf = ToolNeedClassifier(_router(reply))
    assert await clf.needs_tools("something ambiguous") is True


async def test_classifier_fails_safe_on_model_error():
    def boom(messages, info):
        raise RuntimeError("router down")

    clf = ToolNeedClassifier(FunctionModel(boom))
    assert await clf.needs_tools("what's the capital of France") is True
