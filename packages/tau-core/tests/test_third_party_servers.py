"""Third-party MCP servers must be reviewed as deliberately as the ones written here.

Phase 11 built an audit that reads every domain server's source and fails if any tool lacks a CDG
rule or a place on a curated safe-defaults allowlist (test_cdg.py). It scans `_SERVER_MODULES` -
a hardcoded map of packages in THIS repo - because when it was written every server was in this
repo.

Phase 25 broke that assumption by registering `openscad-mcp-server`, which is an npm package. Its
tools are invisible to that audit: they cannot be read from any source file here, so a third-party
server could ship any tool at all and the CDG-coverage test would stay green. That is the same
class of silent gap Phase 11 was created to close, reopened by a one-line registry addition.

This closes it structurally rather than for one server: every entry in servers.yaml must be either
repo-local (and therefore already audited) or declared below with its tools and an explicit
reasoned CDG decision. Registering a third-party server without reviewing it fails a test.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tau_core.cdg import CoreDirectiveGuard, Effect, load_rules

from test_cdg import _PACKAGE_DIR_TO_SERVER_NAME

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
REPO_LOCAL_SERVERS = frozenset(_PACKAGE_DIR_TO_SERVER_NAME.values())


# Third-party servers, their tools, and the decision made about each. Adding a server to
# servers.yaml without adding it here fails test_every_registered_server_is_repo_local_or_declared.
#
# `expected_effect` records what the CDG resolves to TODAY and why that is right - it is a
# decision, not an observation. If a rule is added later that changes one, this test fails and the
# reviewer has to confirm the new answer is intended.
THIRD_PARTY_SERVERS: dict[str, dict] = {
    "openscad-mcp-server": {
        "source": "https://github.com/fboldo/openscad-mcp-server (npm: openscad-mcp-server)",
        "tools": {
            # Both are pure functions: SCAD text in, bytes out. They touch no hardware, allocate
            # no resources, persist nothing, and reach no network. Rendering a preview a human
            # never looks at wastes some CPU and nothing else, so gating either behind the
            # approval queue would add friction with nothing to protect - and would make the
            # design loop (render, look, correct, render again) unusable.
            #
            # The gate that matters is one step later and already exists:
            # fabrication-mcp-server.submit_print_job is require_approval, so no geometry these
            # tools produce reaches the printer without a human. Generating a shape is cheap and
            # reversible; committing filament and machine time is not. That is the same line the
            # draft/promote memory tiers draw.
            "render_scad_png": Effect.ALLOW,
            "export_scad_stl": Effect.ALLOW,
        },
        # Verified live 2026-07-28 through MCPClientManager: connects over stdio via npx, lists
        # exactly these two tools, and rendered a 20mm cube to PNG. Re-check on any version bump.
        "verified_tool_count": 2,
    },
    "stealth-browser-mcp": {
        "source": (
            "https://github.com/vibheksoni/stealth-browser-mcp, pinned to commit "
            "cfede72ed2fa9032eeb59dcb57a9f7e204c63e4f (2026-09-07) in docker-compose.yml's build context"
        ),
        # Verified live 2026-09-11: `docker build` from the pinned commit above, ran the
        # container, and connected with tau-core's real MCPClientManager over streamable_http
        # (not a mock) - list_tools() returned exactly 97 tools, a clean diff (0 undeclared, 0
        # stale) against TOOLS below once get_cookies/export_network_data are set aside as
        # MODEL_EXCLUDED_TOOLS rather than CDG-reviewed. Worth noting: an earlier documentation-
        # only review pass (before this live check) produced an inconsistent list that dropped
        # the dynamic-hooks documentation tools entirely - source-review alone was not trustworthy
        # here, which is exactly why this project's own precedent (see openscad-mcp-server above)
        # requires a live connection, not just reading the code. Re-check on any version bump -
        # test_stealth_browser_mcp_declared_tools_need_live_verification automates this diff,
        # skipped by default since CI has no Chrome+xvfb environment to run the container in.
        "verified_tool_count": 97,
        "tools": {
            # browser-management - lifecycle/navigation, no target-site consequence of its own.
            "spawn_browser": Effect.ALLOW,
            "list_instances": Effect.ALLOW,
            "close_instance": Effect.ALLOW,
            "get_instance_state": Effect.ALLOW,
            "navigate": Effect.ALLOW,
            "go_back": Effect.ALLOW,
            "go_forward": Effect.ALLOW,
            "reload_page": Effect.ALLOW,
            # element-interaction - read-only vs. action split.
            "query_elements": Effect.ALLOW,
            "get_element_state": Effect.ALLOW,
            "wait_for_element": Effect.ALLOW,
            "scroll_page": Effect.ALLOW,
            "get_page_content": Effect.ALLOW,
            "take_screenshot": Effect.ALLOW,
            "click_element": Effect.REQUIRE_APPROVAL,
            "type_text": Effect.REQUIRE_APPROVAL,
            "paste_text": Effect.REQUIRE_APPROVAL,
            "file_upload": Effect.REQUIRE_APPROVAL,
            "select_option": Effect.REQUIRE_APPROVAL,
            "execute_script": Effect.REQUIRE_APPROVAL,
            # element-extraction / file-extraction / progressive-cloning - read + locally-saved
            # inspection of what's already rendered, no target-site consequence.
            "extract_element_styles": Effect.ALLOW,
            "extract_element_structure": Effect.ALLOW,
            "extract_element_events": Effect.ALLOW,
            "extract_element_animations": Effect.ALLOW,
            "extract_element_assets": Effect.ALLOW,
            "extract_element_styles_cdp": Effect.ALLOW,
            "extract_related_files": Effect.ALLOW,
            "clone_element_complete": Effect.ALLOW,
            "extract_complete_element_cdp": Effect.ALLOW,
            "clone_element_to_file": Effect.ALLOW,
            "extract_complete_element_to_file": Effect.ALLOW,
            "extract_element_styles_to_file": Effect.ALLOW,
            "extract_element_structure_to_file": Effect.ALLOW,
            "extract_element_events_to_file": Effect.ALLOW,
            "extract_element_animations_to_file": Effect.ALLOW,
            "extract_element_assets_to_file": Effect.ALLOW,
            "list_clone_files": Effect.ALLOW,
            "cleanup_clone_files": Effect.ALLOW,
            "clone_element_progressive": Effect.ALLOW,
            "expand_styles": Effect.ALLOW,
            "expand_events": Effect.ALLOW,
            "expand_children": Effect.ALLOW,
            "expand_css_rules": Effect.ALLOW,
            "expand_pseudo_elements": Effect.ALLOW,
            "expand_animations": Effect.ALLOW,
            "list_stored_elements": Effect.ALLOW,
            "clear_stored_element": Effect.ALLOW,
            "clear_all_elements": Effect.ALLOW,
            # network-debugging - read/inspect vs. rewrite-live-traffic split. get_cookies and
            # export_network_data are intentionally absent from this dict entirely - they are
            # blocked at the toolset layer (MODEL_EXCLUDED_TOOLS), not review-then-approve.
            "list_network_requests": Effect.ALLOW,
            "get_request_details": Effect.ALLOW,
            "get_response_details": Effect.ALLOW,
            "get_response_content": Effect.ALLOW,
            "search_network_requests": Effect.ALLOW,
            "set_network_capture_filters": Effect.ALLOW,
            "get_network_capture_filters": Effect.ALLOW,
            "modify_headers": Effect.REQUIRE_APPROVAL,
            "import_network_data": Effect.REQUIRE_APPROVAL,
            # cookies-storage - set/clear act on a real session; get_cookies is model-excluded
            # (see above), not part of this dict.
            "set_cookie": Effect.REQUIRE_APPROVAL,
            "clear_cookies": Effect.REQUIRE_APPROVAL,
            # tabs - bookkeeping, no target-site consequence.
            "list_tabs": Effect.ALLOW,
            "switch_tab": Effect.ALLOW,
            "close_tab": Effect.ALLOW,
            "get_active_tab": Effect.ALLOW,
            "new_tab": Effect.ALLOW,
            # cdp-functions - discovery/introspection vs. actual code execution split.
            "list_cdp_commands": Effect.ALLOW,
            "get_execution_contexts": Effect.ALLOW,
            "discover_global_functions": Effect.ALLOW,
            "discover_object_methods": Effect.ALLOW,
            "inspect_function_signature": Effect.ALLOW,
            "get_function_executor_info": Effect.ALLOW,
            "execute_cdp_command": Effect.REQUIRE_APPROVAL,
            "add_script_to_evaluate_on_new_document": Effect.REQUIRE_APPROVAL,
            "call_javascript_function": Effect.REQUIRE_APPROVAL,
            "inject_and_execute_script": Effect.REQUIRE_APPROVAL,
            "create_persistent_function": Effect.REQUIRE_APPROVAL,
            "execute_function_sequence": Effect.REQUIRE_APPROVAL,
            "create_python_binding": Effect.REQUIRE_APPROVAL,
            "execute_python_in_browser": Effect.REQUIRE_APPROVAL,
            # debugging - operational, this server's own process/session, no target-site action.
            "get_debug_view": Effect.ALLOW,
            "clear_debug_view": Effect.ALLOW,
            "export_debug_logs": Effect.ALLOW,
            "get_debug_lock_status": Effect.ALLOW,
            "hot_reload": Effect.ALLOW,
            "reload_status": Effect.ALLOW,
            "validate_browser_environment_tool": Effect.ALLOW,
            # dynamic-hooks - docs/listing vs. actually installing/removing an interceptor.
            "list_dynamic_hooks": Effect.ALLOW,
            "get_dynamic_hook_details": Effect.ALLOW,
            "get_hook_documentation": Effect.ALLOW,
            "get_hook_examples": Effect.ALLOW,
            "get_hook_requirements_documentation": Effect.ALLOW,
            "get_hook_common_patterns": Effect.ALLOW,
            "validate_hook_function": Effect.ALLOW,
            "create_dynamic_hook": Effect.REQUIRE_APPROVAL,
            "create_simple_dynamic_hook": Effect.REQUIRE_APPROVAL,
            "remove_dynamic_hook": Effect.REQUIRE_APPROVAL,
        },
    },
}


def _registered_servers() -> list[str]:
    """Union of servers.yaml (stdio, local dev) and servers.docker.yaml (streamable_http, Docker) -
    Phase 38's stealth-browser-mcp is the first server registered in only the Docker variant (see
    servers.yaml's own comment on why), so checking servers.yaml alone would let a Docker-only
    third-party server skip this whole review mechanism silently."""
    names: set[str] = set()
    for filename in ("servers.yaml", "servers.docker.yaml"):
        data = yaml.safe_load((CONFIG_DIR / filename).read_text(encoding="utf-8")) or {}
        names.update(s["name"] for s in data.get("servers", []))
    return sorted(names)


def test_every_registered_server_is_repo_local_or_declared():
    """The structural check. A third-party server added to servers.yaml with no entry above is
    one whose tools nothing in this repo has ever reviewed."""
    undeclared = [
        name
        for name in _registered_servers()
        if name not in REPO_LOCAL_SERVERS and name not in THIRD_PARTY_SERVERS
    ]
    assert not undeclared, (
        f"Third-party server(s) registered but never reviewed: {undeclared}. Add each to "
        "THIRD_PARTY_SERVERS with its tools and the CDG effect intended for each, or remove it "
        "from servers.yaml. The Phase 11 CDG-coverage audit cannot see these - it reads repo "
        "source - so this is the only thing standing between a third-party tool and "
        "default_effect: allow."
    )


def test_declared_third_party_servers_are_actually_registered():
    """The inverse: a stale declaration for a server nobody runs is misleading documentation."""
    registered = set(_registered_servers())
    stale = [name for name in THIRD_PARTY_SERVERS if name not in registered]
    assert not stale, f"Declared third-party server(s) not in servers.yaml: {stale}"


@pytest.mark.parametrize(
    ("server", "tool", "expected"),
    [
        (server, tool, effect)
        for server, spec in THIRD_PARTY_SERVERS.items()
        for tool, effect in spec["tools"].items()
    ],
    ids=[
        f"{server}.{tool}"
        for server, spec in THIRD_PARTY_SERVERS.items()
        for tool in spec["tools"]
    ],
)
def test_third_party_tool_resolves_to_its_reviewed_effect(server: str, tool: str, expected: Effect):
    guard = CoreDirectiveGuard(load_rules(CONFIG_DIR / "cdg_rules.yaml"))
    actual = guard.evaluate(server, tool).effect
    assert actual is expected, (
        f"{server}.{tool} resolves to {actual.value}, but was reviewed as {expected.value}. "
        "Either a CDG rule changed or the review is out of date - confirm which before editing "
        "this expectation."
    )


@pytest.mark.skip(
    reason=(
        "stealth-browser-mcp needs a real Chrome+xvfb environment this test suite doesn't have, "
        "so this can't run in the normal test venv/CI - already run manually once (2026-09-11, "
        "docker build from the pinned commit + a real MCPClientManager connection over "
        "streamable_http; see THIRD_PARTY_SERVERS' 'Verified live' note). Un-skip and re-run "
        "against `docker compose up stealth-browser-mcp` on any version-pin bump - this is the "
        "check that keeps the declared tool list honest, by diffing it against the real server "
        "instead of trusting a source-code review (which was demonstrably unreliable here once)."
    )
)
async def test_stealth_browser_mcp_declared_tools_need_live_verification():
    """Connects to the real running server (docker compose up stealth-browser-mcp) and asserts
    its actual tool set matches THIRD_PARTY_SERVERS["stealth-browser-mcp"]["tools"] exactly - any
    tool present live but absent here has fallen through to default_effect: allow, unreviewed."""
    from tau_core.mcp_client import MCPClientManager, ServerConfig, ServerRegistry

    registry = ServerRegistry(
        servers=[
            ServerConfig(
                name="stealth-browser-mcp",
                transport="streamable_http",
                url="http://localhost:8000/mcp",
            )
        ]
    )
    async with MCPClientManager(registry) as manager:
        await manager.connect_all()
        live_tools = {t.name for t in await manager.list_tools("stealth-browser-mcp")}

    declared_tools = set(THIRD_PARTY_SERVERS["stealth-browser-mcp"]["tools"])
    # get_cookies/export_network_data are deliberately declared nowhere (MODEL_EXCLUDED_TOOLS
    # instead of a CDG effect) - exclude them from this diff rather than flag them as "missing".
    model_excluded_for_this_server = {"get_cookies", "export_network_data"}

    undeclared = live_tools - declared_tools - model_excluded_for_this_server
    assert not undeclared, (
        f"Live tool(s) not reviewed in THIRD_PARTY_SERVERS: {sorted(undeclared)} - each falls "
        "through to default_effect: allow until added there with a reasoned CDG decision."
    )
    stale = declared_tools - live_tools
    assert not stale, f"Declared tool(s) no longer exist on the live server: {sorted(stale)}"


def test_third_party_tools_do_not_bypass_the_dangerous_name_rules():
    """The CDG's blanket `*shutdown*`/`*destroy*`/`*cdg*` rules match on tool NAME with globs, so
    they cover third-party servers too. Pinned here because that is the only protection that
    applies to a server whose source nobody in this repo controls - if a future version of a
    third-party package ships a `destroy_all` tool, it must still land in the approval queue."""
    guard = CoreDirectiveGuard(load_rules(CONFIG_DIR / "cdg_rules.yaml"))
    for server in THIRD_PARTY_SERVERS:
        assert guard.evaluate(server, "shutdown_host").effect is Effect.REQUIRE_APPROVAL
        assert guard.evaluate(server, "destroy_everything").effect is Effect.REQUIRE_APPROVAL
        assert guard.evaluate(server, "edit_cdg_rules").effect is Effect.DENY
