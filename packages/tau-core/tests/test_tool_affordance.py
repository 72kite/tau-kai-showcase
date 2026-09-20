"""Tool descriptions must be findable by intent, not just accurate.

**What this test can and cannot prove.** It cannot prove the model will pick the right tool -
only `examples/eval_tool_selection.py` against a live Ollama can do that, and it costs minutes
per run. What this does is cheap, offline, and deterministic: it asserts that the vocabulary a
user actually uses appears in the description of the tool that serves that intent. That is a
*necessary* condition for the model to find the tool, not a sufficient one.

It exists because the 2026-07-22 eval failures were not random. `call_service` was described as
"Call a Home Assistant service for routine domains" - accurate, and written for someone who
already knew Home Assistant files light-switching under "service call". The model answered "None
of the provided functions directly correspond to turning on a kitchen light" with that exact tool
in its roster. Accuracy was never the problem; findability was.

Phase 44 moved the "turn on the kitchen light" intent off `call_service` entirely, onto new
`turn_on`/`turn_off`/`set_light_state`/`set_climate_temperature` tools that resolve a device/area
by name through Home Assistant's own intent system - so the vocabulary this test guards moved with
it. `call_service` itself is now the narrower escape hatch for what those four don't cover.

So this guards the property that fix established, in the direction it can actually regress: a
future edit that tightens a description back toward implementation vocabulary and quietly drops
the user's words. That regression is invisible in every other test in this repo - the tool still
works, every unit test still passes, and selection accuracy silently falls until someone next
spends an hour running the live eval.

Read directly from each package's source, so no package needs to be installed (same approach as
test_cdg.py's CDG-coverage audit, whose module-path map this reuses rather than duplicating).
"""

from __future__ import annotations

import ast

import pytest

from test_cdg import (  # the same source-of-truth map the CDG coverage audit uses
    _PACKAGE_DIR_TO_SERVER_NAME,
    _SERVER_MODULES,
    PACKAGES_DIR,
)

# (server, tool) -> phrases a user would plausibly say when they want this tool. Every phrase must
# appear somewhere in that tool's description, case-insensitively.
#
# Scoped deliberately to the tools the live eval actually exercises. A repo-wide "every tool must
# contain N intent phrases" rule would be a bad trade: it would force ceremony onto internal tools
# no user ever asks for by name (ui-bridge's update_* setters are called by tau-core, never
# requested in words), and a rule that has to be suppressed everywhere stops being read.
INTENT_VOCABULARY: dict[tuple[str, str], tuple[str, ...]] = {
    # The headline failure: the model could not get from "turn on the kitchen light" to a tool
    # described in Home Assistant's own API vocabulary. Phase 44 gave that intent its own tools
    # instead of trying to make call_service's single description cover everything.
    ("home-assistant-mcp-server", "turn_on"): ("turn on", "kitchen", "light"),
    ("home-assistant-mcp-server", "turn_off"): ("turn off", "porch", "light"),
    ("home-assistant-mcp-server", "set_light_state"): ("dim", "lounge", "light"),
    ("home-assistant-mcp-server", "set_climate_temperature"): ("thermostat", "bedroom", "degrees"),
    # Failed because "kitchen lights" pulled the model toward device control; the user's actual
    # verb ("remember") now leads the description.
    ("memory-mcp-server", "draft_memory"): (
        "remember", "don't forget", "preference",
    ),
    # The tool name says "intrusion"; every human asks about "lockdown".
    ("security-mcp-server", "get_intrusion_status"): (
        "lockdown", "secure",
    ),
    # Description was already near-verbatim and still failed - the model invented
    # `system-mcp-server__list_vms`. Naming the server in the text is cheap insurance.
    ("proxmox-mcp-server", "list_vms"): (
        "list the vms", "container", "running", "proxmox-mcp-server",
    ),
    # Lost to utility-mcp-server__get_system_status, by the model's own account.
    ("fabrication-mcp-server", "get_printer_status"): (
        "3d printer", "printing", "temperature",
    ),
    ("vision-mcp-server", "get_snapshot"): (
        "camera", "snapshot", "see",
    ),
}

# Tools that must explicitly disclaim the domains they were caught poaching. These two are
# general-purpose "status"/"capability" tools sitting in a roster full of specific ones, which
# makes them plausible matches for almost any question - `get_system_status` really did answer
# "What's the status of the 3D printer?" in the eval. Saying what a tool is NOT for is the only
# thing that separates it from its neighbours.
EXCLUSION_VOCABULARY: dict[tuple[str, str], tuple[str, ...]] = {
    ("utility-mcp-server", "get_system_status"): ("printer", "camera", "not for"),
    ("utility-mcp-server", "describe_capabilities"): ("not",),
    # Phase 44: a live eval caught a 3b model reaching for this tool on "switch off the porch
    # light"/"dim the lounge lamps" - pattern-matching the update_..._state name shape shared with
    # three OTHER ui-bridge tools already excluded from the model's toolset entirely (this one
    # can't be, since it's the model's real, intended way to report a face-recognition event).
    ("ui-bridge-mcp-server", "update_recognition_state"): ("not for", "light"),
}


def _tool_docstrings(source: str) -> dict[str, str]:
    """{tool_name: docstring} for every @mcp.tool()/@server.tool() in a module.

    AST rather than a regex: a docstring is exactly what this test reads, and regexing a
    multi-line triple-quoted string out of source is how you end up asserting against half of one.
    """
    tree = ast.parse(source)
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        decorated = any(
            isinstance(dec, ast.Call)
            and isinstance(dec.func, ast.Attribute)
            and dec.func.attr == "tool"
            for dec in node.decorator_list
        )
        if decorated:
            found[node.name] = ast.get_docstring(node) or ""
    return found


def _description(server_name: str, tool_name: str) -> str:
    for pkg_dir, rel_path in _SERVER_MODULES.items():
        if _PACKAGE_DIR_TO_SERVER_NAME[pkg_dir] != server_name:
            continue
        path = PACKAGES_DIR / pkg_dir / rel_path
        docstrings = _tool_docstrings(path.read_text(encoding="utf-8"))
        if tool_name not in docstrings:
            pytest.fail(f"{server_name}.{tool_name} not found in {path} - was it renamed?")
        return docstrings[tool_name]
    pytest.fail(f"No source module mapped for server {server_name!r}")


@pytest.mark.parametrize(
    ("server", "tool", "phrases"),
    [(s, t, p) for (s, t), p in INTENT_VOCABULARY.items()],
    ids=[f"{s}.{t}" for s, t in INTENT_VOCABULARY],
)
def test_description_speaks_the_users_language(server: str, tool: str, phrases: tuple[str, ...]):
    description = _description(server, tool).lower()
    missing = [phrase for phrase in phrases if phrase.lower() not in description]
    assert not missing, (
        f"{server}.{tool}'s description no longer contains {missing}. These are the words a user "
        f"actually says when they want this tool; a description that drops them is accurate and "
        f"unfindable. See the 2026-07-22 eval failures in project-tau-plan.md §8.23."
    )


@pytest.mark.parametrize(
    ("server", "tool", "phrases"),
    [(s, t, p) for (s, t), p in EXCLUSION_VOCABULARY.items()],
    ids=[f"{s}.{t}" for s, t in EXCLUSION_VOCABULARY],
)
def test_general_tools_say_what_they_are_not_for(server: str, tool: str, phrases: tuple[str, ...]):
    description = _description(server, tool).lower()
    missing = [phrase for phrase in phrases if phrase.lower() not in description]
    assert not missing, (
        f"{server}.{tool} is a general-purpose tool in a roster of specific ones, so it needs to "
        f"say what it does NOT cover; it is missing {missing}. Without that it reads as a "
        f"plausible answer to any question containing 'status' or 'what can you do'."
    )


@pytest.mark.parametrize(
    ("server", "tool"),
    list(INTENT_VOCABULARY),
    ids=[f"{s}.{t}" for s, t in INTENT_VOCABULARY],
)
def test_first_line_is_user_facing_not_implementation_facing(server: str, tool: str):
    """The first line carries the most weight in tool selection, so it must describe the user's
    goal rather than the API being wrapped.

    The banned openers are the exact shapes that failed: "Call a Home Assistant service…",
    "Get current printer status…", "Capture a snapshot…" all lead with the implementation's verb.
    """
    first_line = _description(server, tool).strip().splitlines()[0].strip().lower()
    banned_openers = ("call a ", "call the ", "get current ", "get a ", "get the ", "list all ")
    offender = next((opener for opener in banned_openers if first_line.startswith(opener)), None)
    assert offender is None, (
        f"{server}.{tool}'s description opens with {offender!r} - that is implementation "
        f"vocabulary. Lead with what the user is trying to do. First line was: {first_line!r}"
    )
