"""Phase 23: per-turn server scoping, and proof the refactor under it changed nothing.

Two separable things are tested here, and the first matters more than the second:

1. **The tool-need gate still behaves exactly as it did.** Phase 23 turned tool_need's flat
   trigger list into a per-server map. That is a refactor of a *safety* mechanism - the thing that
   decides whether a turn gets tools at all - so "the union is unchanged" is not a detail to trust
   by eye. It is asserted directly.
2. **Scoping fails open.** Every uncertain case must widen to the full roster, never narrow to a
   guess, because an excluded server cannot be called at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.models.function import FunctionModel

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.llm.tool_need import (
    ALWAYS_AVAILABLE_SERVERS,
    SERVER_TRIGGER_WORDS,
    heuristic_servers_needed,
    heuristic_tool_need,
)

ALL_SERVERS = sorted(SERVER_TRIGGER_WORDS)
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


# --- 1. the refactor preserved the existing gate ----------------------------------------------

# Every phrase in the flat list that existed before Phase 23, one per group, plus the cases the
# original module's own docstrings called out. If the regrouping dropped or mangled a pattern,
# something here stops matching.
@pytest.mark.parametrize(
    "text",
    [
        "turn on the kitchen light", "unlock the front door", "is the garage open",
        "what's the temperature", "dim the lamps", "list the vms", "how's the cluster",
        "any cve advisories", "check the camera", "take a snapshot", "who's at the door",
        "are we in lockdown", "was there an intrusion", "is the house secure",
        "how's the 3d printer", "check the filament", "what's the drone's telemetry",
        "start a patrol", "propose a change to your code", "what time is it",
        "what's today's date", "how much ram do you have", "which model are you",
        "what can you do", "search the web for that", "look up the price",
        "what's the latest news", "remember that I like tea", "don't forget my birthday",
    ],
)
def test_known_trigger_phrases_still_force_the_full_path(text: str):
    assert heuristic_tool_need(text) == "tools", (
        f"{text!r} no longer triggers the toolset path - the Phase 23 regrouping dropped a pattern"
    )


@pytest.mark.parametrize(
    "text",
    ["hello", "hi there", "thanks!", "tell me a joke", "how are you", "what is 17 * 23",
     "calculate 4+4", "how much is 12/3"],
)
def test_tool_free_cases_are_still_tool_free(text: str):
    assert heuristic_tool_need(text) == "no_tools"


@pytest.mark.parametrize("text", ["what is 17 times 23", "what's the square root of 9"])
def test_worded_arithmetic_still_defers_to_the_classifier(text: str):
    """Pre-existing behaviour, pinned rather than "fixed": `_is_pure_math` matches operator forms
    ("17 * 23"), not English ones ("17 times 23"), so worded arithmetic returns "uncertain" and
    pays for one cheap classifier call. That is the safe direction and it is what the live eval
    measured against, so Phase 23's refactor must not quietly alter it - widening the math regex
    is a behaviour change that belongs in its own measured commit, not smuggled into a regroup."""
    assert heuristic_tool_need(text) == "uncertain"


def test_every_server_group_is_non_empty_and_compiles():
    """A group that silently became empty would make its server unreachable by scoping while
    leaving the tool-need gate looking fine, since the union would barely change."""
    for server, words in SERVER_TRIGGER_WORDS.items():
        assert words, f"{server} has no trigger words"


# --- 2. the design-vocabulary gap this refactor found ------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "draw me a mounting bracket",
        "sketch a wiring diagram for this",
        "design an enclosure for the pi",
        "can you lay out a simple circuit",
    ],
)
def test_design_requests_now_reach_the_toolset(text: str):
    """Pre-existing bug, found while keying the vocabulary by server: MAIN_SYSTEM_PROMPT has a
    whole section instructing the model to publish design work via
    ui-bridge-mcp-server__update_design_state, and not one of "draw"/"sketch"/"design"/"diagram"
    was a trigger word. These fell through to the classifier, whose prompt never mentions drawing
    either - so a design request could take Phase 18's tool-free path, where the tool it needs
    does not exist and the model can only describe a drawing it failed to publish."""
    assert heuristic_tool_need(text) == "tools"
    scope = heuristic_servers_needed(text, ALL_SERVERS)
    assert scope is None or "ui-bridge-mcp-server" in scope


# --- 3. scoping fails open ---------------------------------------------------------------------


def test_unrecognised_request_uses_every_server():
    """The most important rule. An unrecognised request is when a guess is least informed AND
    least recoverable, so it must widen rather than narrow."""
    assert heuristic_servers_needed("do the thing with the stuff", ALL_SERVERS) is None
    assert heuristic_servers_needed("", ALL_SERVERS) is None


def test_a_broad_match_declines_to_scope():
    """A request touching most domains saves few tokens and risks dropping the right server."""
    text = "check the lights, the vms, the camera, the printer, the drone and the lockdown"
    assert heuristic_servers_needed(text, ALL_SERVERS) is None


def test_scope_is_intersected_with_registered_servers():
    """A scope naming a server this deployment doesn't run must degrade to the ones it does -
    build_toolset raises ValueError on an unknown server name, and a turn must never die because
    the vocabulary knows about a server the operator never installed."""
    registered = ["home-assistant-mcp-server", "memory-mcp-server", "ui-bridge-mcp-server",
                  "utility-mcp-server", "vision-mcp-server", "research-mcp-server"]
    scope = heuristic_servers_needed("turn on the kitchen light", registered)
    assert scope is not None
    assert scope <= set(registered)


def test_a_tiny_deployment_declines_to_scope():
    """On a 3-server install there is nothing worth saving, and 'half the roster' is one server."""
    assert heuristic_servers_needed(
        "turn on the kitchen light", ["home-assistant-mcp-server", "memory-mcp-server", "utility-mcp-server"]
    ) is None


# --- 4. scoping actually narrows when it should ------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("turn on the kitchen light", "home-assistant-mcp-server"),
        ("are we in lockdown right now", "security-mcp-server"),
        ("list the vms on the cluster", "proxmox-mcp-server"),
        ("how's the 3d printer doing", "fabrication-mcp-server"),
        ("what's the drone's battery", "robotics-mcp-server"),
        ("take a snapshot from the camera", "vision-mcp-server"),
        ("search the web for the newest rust release", "research-mcp-server"),
    ],
)
def test_a_focused_request_scopes_to_its_own_domain(text: str, expected: str):
    scope = heuristic_servers_needed(text, ALL_SERVERS)
    assert scope is not None, f"{text!r} should have scoped"
    assert expected in scope
    assert len(scope) < len(ALL_SERVERS)


def test_always_available_servers_survive_scoping():
    """draft_memory is instructed proactively on any turn, and the design/recognition tools are
    reached as a consequence of another tool's result - neither is predictable from wording."""
    scope = heuristic_servers_needed("turn on the kitchen light", ALL_SERVERS)
    assert scope is not None
    assert ALWAYS_AVAILABLE_SERVERS <= scope


def test_scoping_a_memory_turn_keeps_memory():
    scope = heuristic_servers_needed("remember that I like tea", ALL_SERVERS)
    assert scope is not None
    assert "memory-mcp-server" in scope


# --- 4b. Phase 25: the first third-party server ------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "design me a mounting bracket for the camera, 40mm wide",
        "model a printable case for the pi",
        "export that as an STL",
        "make me a parametric enclosure",
    ],
)
def test_cad_requests_reach_the_openscad_server(text: str):
    scope = heuristic_servers_needed(text, ALL_SERVERS)
    assert scope is None or "openscad-mcp-server" in scope


def test_a_cad_request_also_keeps_ui_bridge():
    """The natural flow is "model it, then show me" - openscad renders, ui-bridge publishes to the
    kiosk panel. Scoping unions every matched group rather than picking a winner, and the two
    vocabularies overlap deliberately so a design turn gets both."""
    scope = heuristic_servers_needed("design me a mounting bracket, 40mm wide", ALL_SERVERS)
    assert scope is not None
    assert {"openscad-mcp-server", "ui-bridge-mcp-server"} <= scope


def test_a_flat_drawing_request_does_not_pull_in_the_cad_server():
    """The discrimination worth having: a 2D wiring diagram is an SVG for the design panel, not a
    3D model. Keeping openscad out of that turn is the whole point of scoping - fewer irrelevant
    schemas is what took the eval from 13/37 to 27/37."""
    scope = heuristic_servers_needed("draw a wiring diagram", ALL_SERVERS)
    assert scope is not None
    assert "ui-bridge-mcp-server" in scope
    assert "openscad-mcp-server" not in scope


# --- 5. the two bugs the offline measurement caught --------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Has there been a break-in today?",
        "did someone break in last night",
        "I think there's a burglar",
        "has anything been stolen",
    ],
)
def test_break_in_wording_reaches_the_security_server(text: str):
    """The bug that made the case for measuring rather than eyeballing. The security group knew
    "lockdown", "intrusion", "breach" and "secure" - none of which is how anyone actually asks. So
    "Has there been a break-in today?" matched only utility's "today", scoped confidently to
    {utility, memory, ui-bridge}, and produced a turn where the security server was not merely
    unlikely to be called but ABSENT."""
    scope = heuristic_servers_needed(text, ALL_SERVERS)
    assert scope is None or "security-mcp-server" in scope


@pytest.mark.parametrize(
    "text",
    ["is it done today", "what's happening tomorrow", "anything new tonight", "what's the latest"],
)
def test_a_generic_time_adverb_alone_never_narrows_the_roster(text: str):
    """The structural half of the same bug. "today"/"tomorrow"/"tonight"/"latest" attach to every
    domain equally, so on their own they are evidence that a tool is needed (the tool-need gate
    still fires) but NOT evidence about which one. Narrowing on them means any question about any
    domain can be scoped to the wrong server with full confidence."""
    assert heuristic_tool_need(text) == "tools"
    assert heuristic_servers_needed(text, ALL_SERVERS) is None


# --- 6. the feature flag ------------------------------------------------------------------------


class _FakeManager:
    """Minimal MCPClientManager stand-in: _scope_servers only ever asks for the roster."""

    def __init__(self, servers: list[str]):
        self._servers = servers

    def registered_servers(self) -> list[str]:
        return list(self._servers)


def _assistant(scope_enabled: bool) -> TauAssistant:
    settings = TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        approval_store_path=None,
        scope_servers_per_turn=scope_enabled,
    )
    host = TauCoreHost(_FakeManager(ALL_SERVERS), settings=settings)
    model = FunctionModel(lambda *_a, **_k: None)
    return TauAssistant(host, main_model=model, router_model=model)


def test_flag_off_is_a_no_op():
    """The property the whole A/B rests on. With TAU_SCOPE_SERVERS_PER_TURN unset, Phase 23 must
    add nothing to the turn but one boolean check - returning None here is what makes
    build_toolset fall back to every registered server, i.e. exactly the pre-Phase-23 path. If
    this ever fails, the "off" arm of the experiment is not measuring the old behaviour and the
    comparison against §10's numbers is meaningless."""
    assistant = _assistant(scope_enabled=False)
    for text in ["turn on the kitchen light", "are we in lockdown", "hello there", ""]:
        assert assistant._scope_servers(text) is None


def test_flag_on_narrows_a_focused_turn():
    assistant = _assistant(scope_enabled=True)
    scope = assistant._scope_servers("turn on the kitchen light")
    assert scope is not None
    assert "home-assistant-mcp-server" in scope
    assert len(scope) < len(ALL_SERVERS)


def test_flag_on_still_falls_open_on_an_unrecognised_turn():
    assistant = _assistant(scope_enabled=True)
    assert assistant._scope_servers("do the thing with the stuff") is None


def test_scope_never_names_a_server_the_deployment_lacks():
    """build_toolset raises ValueError on an unknown server name, so a scope containing one would
    turn a normal chat turn into a 502. Guarded here at the seam that actually calls it."""
    settings = TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        approval_store_path=None,
        scope_servers_per_turn=True,
    )
    registered = ["home-assistant-mcp-server", "memory-mcp-server", "ui-bridge-mcp-server",
                  "vision-mcp-server", "utility-mcp-server", "research-mcp-server"]
    host = TauCoreHost(_FakeManager(registered), settings=settings)
    model = FunctionModel(lambda *_a, **_k: None)
    assistant = TauAssistant(host, main_model=model, router_model=model)
    scope = assistant._scope_servers("turn on the kitchen light")
    assert scope is None or set(scope) <= set(registered)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("what time is it", "utility-mcp-server"),
        ("what's today's date", "utility-mcp-server"),
        ("is the printer done today", "fabrication-mcp-server"),
    ],
)
def test_weak_triggers_do_not_block_a_genuine_match(text: str, expected: str):
    """The control for the rule above - it must not overshoot. "time" and "date" are real signals
    for get_time/get_date and stay strong; and a weak word sitting next to a domain word ("is the
    printer done today") must still scope to that domain rather than falling open."""
    scope = heuristic_servers_needed(text, ALL_SERVERS)
    assert scope is not None, f"{text!r} should still scope"
    assert expected in scope
