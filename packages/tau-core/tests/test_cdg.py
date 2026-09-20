from pathlib import Path

import pytest

from tau_core.cdg import (
    ApprovalRequiredError,
    ApprovedAction,
    CoreDirectiveGuard,
    CoreDirectiveViolation,
    Effect,
    Rule,
    RuleSet,
    load_rules,
)
from tau_core.hashing import hash_arguments

REPO_RULESET_PATH = Path(__file__).resolve().parent.parent / "config" / "cdg_rules.yaml"


def make_ruleset(*rules: Rule, default_effect: Effect = Effect.ALLOW) -> RuleSet:
    return RuleSet(default_effect=default_effect, rules=list(rules))


def test_default_allow_when_no_rules_match():
    guard = CoreDirectiveGuard(make_ruleset())
    decision = guard.evaluate("home-assistant-mcp-server", "get_entity_state")
    assert decision.effect is Effect.ALLOW


def test_deny_rule_blocks_call_outright():
    ruleset = make_ruleset(
        Rule(id="deny-cdg", server="*", tool="*cdg*", effect=Effect.DENY, reason="never touch the guard")
    )
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(CoreDirectiveViolation) as exc_info:
        guard.enforce("meta-mcp-server", "edit_cdg_rules", {})
    assert exc_info.value.rule_id == "deny-cdg"


def test_require_approval_blocks_without_token():
    ruleset = make_ruleset(
        Rule(id="shutdown", server="*", tool="*shutdown*", effect=Effect.REQUIRE_APPROVAL, reason="needs a human")
    )
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("proxmox-mcp-server", "shutdown_host", {"vmid": 101})


def test_require_approval_passes_with_matching_token():
    ruleset = make_ruleset(
        Rule(id="shutdown", server="*", tool="*shutdown*", effect=Effect.REQUIRE_APPROVAL, reason="needs a human")
    )
    guard = CoreDirectiveGuard(ruleset)
    args = {"vmid": 101}
    approval = ApprovedAction(
        request_id="req-1",
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments_hash=hash_arguments(args),
        approved_by="zion",
    )
    decision = guard.enforce("proxmox-mcp-server", "shutdown_host", args, approval=approval)
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_approval_token_bound_to_exact_arguments():
    """An approval granted for one set of arguments must not authorize a call with different arguments."""
    ruleset = make_ruleset(
        Rule(id="shutdown", server="*", tool="*shutdown*", effect=Effect.REQUIRE_APPROVAL, reason="needs a human")
    )
    guard = CoreDirectiveGuard(ruleset)
    approval = ApprovedAction(
        request_id="req-1",
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments_hash=hash_arguments({"vmid": 101}),
        approved_by="zion",
    )
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("proxmox-mcp-server", "shutdown_host", {"vmid": 999}, approval=approval)


def test_approval_token_bound_to_exact_server_and_tool():
    ruleset = make_ruleset(
        Rule(id="all-approval", server="*", tool="*", effect=Effect.REQUIRE_APPROVAL, reason="test")
    )
    guard = CoreDirectiveGuard(ruleset)
    approval = ApprovedAction(
        request_id="req-1",
        server="proxmox-mcp-server",
        tool="shutdown_host",
        arguments_hash=hash_arguments({}),
        approved_by="zion",
    )
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("robotics-mcp-server", "shutdown_host", {}, approval=approval)


def test_first_matching_rule_wins():
    ruleset = make_ruleset(
        Rule(id="specific-allow", server="proxmox-mcp-server", tool="list_vms", effect=Effect.ALLOW, reason="safe"),
        Rule(id="general-deny", server="proxmox-mcp-server", tool="*", effect=Effect.DENY, reason="lockdown"),
    )
    guard = CoreDirectiveGuard(ruleset)
    decision = guard.evaluate("proxmox-mcp-server", "list_vms")
    assert decision.effect is Effect.ALLOW
    assert decision.rule_id == "specific-allow"


def test_load_rules_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_rules("does-not-exist.yaml")


def test_repo_ruleset_loads_and_denies_cdg_self_modification():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(CoreDirectiveViolation):
        guard.enforce("any-server", "edit_cdg_rules", {})


def test_repo_ruleset_requires_approval_for_shutdown():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("proxmox-mcp-server", "shutdown_vm", {"vmid": 100})


def test_repo_ruleset_allows_benign_call():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    decision = guard.enforce("home-assistant-mcp-server", "get_entity_state", {"entity_id": "light.kitchen"})
    assert decision.effect is Effect.ALLOW


def test_repo_ruleset_requires_approval_for_robotics_patrol():
    """Starting a patrol (physical actuation) must require approval, per Phase 5's
    'pre-approved patrol routes + human sign-off' constraint."""
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("robotics-mcp-server", "patrol_route", {"route_name": "perimeter"})


def test_repo_ruleset_allows_robotics_emergency_stop_without_approval():
    """Emergency stop must be immediate - the specific allow rule has to be ordered before the
    blanket robotics require_approval rule, or this would incorrectly block on approval."""
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    decision = guard.enforce("robotics-mcp-server", "emergency_stop", {})
    assert decision.effect is Effect.ALLOW


def test_repo_ruleset_allows_robotics_telemetry_read_without_approval():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    decision = guard.enforce("robotics-mcp-server", "get_telemetry", {})
    assert decision.effect is Effect.ALLOW


def test_repo_ruleset_allows_utility_tools_without_approval():
    """Phase 6.B: utility-mcp-server's tools are all read-only self-knowledge/primitives, so
    they must fall through to default_effect: allow - no CDG rule, no approval prompt. If a
    future utility tool ever needs gating, this test is where that assumption gets revisited."""
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    for tool in ("get_time", "get_date", "get_system_status", "describe_capabilities"):
        decision = guard.enforce("utility-mcp-server", tool, {})
        assert decision.effect is Effect.ALLOW, tool


def test_repo_ruleset_requires_approval_for_storing_memory():
    """Phase 2.8's Memory Tree Engine: storing new project/context memory must require the
    same human sign-off as face/voice enrollment (store_face/store_voice)."""
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("memory-mcp-server", "store_memory", {"title": "x", "content": "y"})


def test_repo_ruleset_allows_reading_memory_without_approval():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    decision = guard.enforce("memory-mcp-server", "search_memory", {"query": "x"})
    assert decision.effect is Effect.ALLOW


def test_repo_ruleset_requires_approval_for_setting_wake_word():
    """2026-09-08: switching the local wake word changes what every device in the household
    listens for, not just a private setting - same bar as memory-access-level-change-needs-
    approval, and the CDG has no caller-identity concept, so this applies even to a verified
    admin clicking a Settings button, not just Tau proposing it on its own."""
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("voice-mcp-server", "set_wake_word", {"wake_id": "hey_jarvis"})


def test_repo_ruleset_allows_listing_wake_words_without_approval():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    decision = guard.enforce("voice-mcp-server", "list_wake_words", {})
    assert decision.effect is Effect.ALLOW


def test_repo_ruleset_requires_approval_for_merge_proposal():
    """Phase 11 audit: merge_proposal - the tool that actually deploys a Tau-authored change -
    had no rule at all and fell through to default_effect: allow. ProposalStore.merge_proposal()
    already checks is_ready_to_merge() (unanimous reviewer-agent approval or an override), but
    agents are not a human in the loop; this is the structural CDG backstop."""
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    with pytest.raises(ApprovalRequiredError):
        guard.enforce("phase4-mcp-server", "merge_proposal", {"proposal_id": "p1"})


# --- CDG coverage audit (Phase 11) -----------------------------------------------------------
#
# Every prior test above spot-checks ONE tool it already knew to worry about - which is exactly
# how merge_proposal and get_unified_transcript went unnoticed: nothing checked the *other* 75
# tools for a rule anyone forgot. This walks the real source of every domain server (by path, not
# by import - so it needs none of them pip-installed) and asserts every tool it finds is either
# covered by an explicit rule or is on ALLOWLISTED_DEFAULT_TOOLS below, a curated list of tools
# this project has deliberately left to default_effect: allow, each for a documented reason (see
# each package's own server.py docstring for the real justification - this list is the audit
# trail of *that a human looked*, not the reasoning itself).
#
# A tool that is neither ruled nor allowlisted fails this test. Adding a genuinely-safe new tool
# means adding it to ALLOWLISTED_DEFAULT_TOOLS deliberately, not this test silently staying green.

PACKAGES_DIR = REPO_RULESET_PATH.resolve().parents[2]

# (package directory name, path to its server module, relative to that package dir)
_SERVER_MODULES = {
    "fabrication-mcp-server": "src/fabrication_mcp_server/server.py",
    "forgejo-mcp-server": "src/forgejo_mcp_server/server.py",
    "home-assistant-mcp-server": "src/ha_mcp_server/server.py",
    "memory-mcp-server": "src/memory_mcp_server/server.py",
    "phase4-upgrade-pipeline": "phase4_server.py",
    "proxmox-mcp-server": "src/proxmox_mcp_server/server.py",
    "research-mcp-server": "src/research_mcp_server/server.py",
    "robotics-mcp-server": "src/robotics_mcp_server/server.py",
    "security-mcp-server": "src/security_mcp_server/server.py",
    "ui-bridge-mcp-server": "src/ui_bridge_mcp_server/server.py",
    "utility-mcp-server": "src/utility_mcp_server/server.py",
    "vision-mcp-server": "src/vision_mcp_server/server.py",
    "voice-mcp-server": "src/voice_mcp_server/server.py",
    "wikipedia-mcp-server": "src/wikipedia_mcp_server/server.py",
}

# server.py's own MCP server name may differ from the package directory name (e.g.
# home-assistant-mcp-server's package is ha_mcp_server, but it registers as
# "home-assistant-mcp-server" - the name servers.yaml and the CDG both use).
_PACKAGE_DIR_TO_SERVER_NAME = {
    "fabrication-mcp-server": "fabrication-mcp-server",
    "forgejo-mcp-server": "forgejo-mcp-server",
    "home-assistant-mcp-server": "home-assistant-mcp-server",
    "memory-mcp-server": "memory-mcp-server",
    "phase4-upgrade-pipeline": "phase4-mcp-server",
    "proxmox-mcp-server": "proxmox-mcp-server",
    "research-mcp-server": "research-mcp-server",
    "robotics-mcp-server": "robotics-mcp-server",
    "security-mcp-server": "security-mcp-server",
    "ui-bridge-mcp-server": "ui-bridge-mcp-server",
    "utility-mcp-server": "utility-mcp-server",
    "vision-mcp-server": "vision-mcp-server",
    "voice-mcp-server": "voice-mcp-server",
    "wikipedia-mcp-server": "wikipedia-mcp-server",
}

# Read-only queries, output-only actions, or explicitly-documented-safe writes (each one's own
# server.py docstring or the design notes in project-tau-plan.md explain why). Reviewed 2026-07-19.
ALLOWLISTED_DEFAULT_TOOLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("fabrication-mcp-server", "get_printer_status"),
        ("fabrication-mcp-server", "pause_print"),
        ("fabrication-mcp-server", "resume_print"),
        ("forgejo-mcp-server", "list_repos"),
        ("forgejo-mcp-server", "get_repo"),
        ("forgejo-mcp-server", "list_commits"),
        ("forgejo-mcp-server", "list_issues"),
        ("home-assistant-mcp-server", "list_devices"),
        ("home-assistant-mcp-server", "get_entity_state"),
        # Non-security domains only (lock/alarm/cover split out as call_security_service, which
        # IS gated) - see home-assistant-mcp-server/tests/test_server.py's own docstring.
        ("home-assistant-mcp-server", "call_service"),
        # Phase 44: same non-security-domains-only reasoning as call_service above.
        # turn_on/turn_off validate their own `domain` argument against INTENT_ALLOWED_DOMAINS
        # (excludes lock/alarm_control_panel/cover) in Python, the same way call_service validates
        # against SECURITY_SENSITIVE_DOMAINS - the CDG itself still can't see arguments, only names.
        ("home-assistant-mcp-server", "turn_on"),
        ("home-assistant-mcp-server", "turn_off"),
        # Always light-only / always climate-only by construction (HA's own HassLightSet /
        # HassClimateSetTemperature intents) - no domain argument exists to gate.
        ("home-assistant-mcp-server", "set_light_state"),
        ("home-assistant-mcp-server", "set_climate_temperature"),
        ("memory-mcp-server", "match_face"),
        ("memory-mcp-server", "match_voice"),
        ("memory-mcp-server", "get_person_profile"),
        ("memory-mcp-server", "list_people"),
        ("memory-mcp-server", "set_person_portrait"),  # cosmetic cache, not new biometric data
        ("memory-mcp-server", "search_memory"),
        ("memory-mcp-server", "get_memory_tree"),
        ("memory-mcp-server", "reinforce_memory"),  # only adjusts an existing node's score
        ("memory-mcp-server", "draft_memory"),  # covered by an explicit allow rule too
        ("memory-mcp-server", "discard_draft"),  # covered by an explicit allow rule too
        ("proxmox-mcp-server", "list_vms"),
        ("proxmox-mcp-server", "get_vm_status"),
        ("proxmox-mcp-server", "check_cve_advisories"),
        ("research-mcp-server", "search_web"),
        ("research-mcp-server", "fetch_page"),  # CDG gates what happens after reading, not reading
        ("research-mcp-server", "search_images"),  # Phase 40: read-only, same reasoning as search_web
        ("security-mcp-server", "get_intrusion_status"),
        ("security-mcp-server", "log_incident"),  # append-only, not destructive
        # These four (update_transcription/update_security_state/update_devices_state/
        # update_vision_state) are also kept from the model at the toolset layer now (Phase 41 -
        # tau_core.llm.toolset.MODEL_EXCLUDED_TOOLS, same reasoning as get_unified_transcript/
        # list_drafts below), but the CDG still resolves them to ALLOW for their real internal
        # callers (tau-core's own chat bridge, other domain servers) - hence still listed here.
        ("ui-bridge-mcp-server", "update_transcription"),
        ("ui-bridge-mcp-server", "get_device_transcript"),
        ("ui-bridge-mcp-server", "update_security_state"),
        ("ui-bridge-mcp-server", "update_devices_state"),
        ("ui-bridge-mcp-server", "update_vision_state"),
        ("ui-bridge-mcp-server", "update_design_state"),
        ("ui-bridge-mcp-server", "clear_design_state"),
        ("ui-bridge-mcp-server", "update_recognition_state"),
        ("ui-bridge-mcp-server", "clear_recognition_state"),
        ("ui-bridge-mcp-server", "refresh_approval_queue"),
        ("ui-bridge-mcp-server", "mark_approval_viewed"),
        # get_unified_transcript / list_drafts deliberately do NOT appear here - they're kept
        # from the model at the toolset layer (tau_core.llm.toolset.MODEL_EXCLUDED_TOOLS), but
        # the CDG itself still resolves them to ALLOW (see that module's docstring for why a CDG
        # rule would break their legitimate admin-only HTTP callers). They stay off this
        # allowlist on purpose, so this test keeps failing loudly for anyone who forgets that and
        # tries to "fix" it with a CDG rule instead.
        ("utility-mcp-server", "get_time"),
        ("utility-mcp-server", "get_date"),
        ("utility-mcp-server", "get_system_status"),
        ("utility-mcp-server", "describe_capabilities"),
        ("vision-mcp-server", "get_snapshot"),
        ("vision-mcp-server", "describe_scene"),
        ("vision-mcp-server", "detect_faces"),
        ("vision-mcp-server", "track_object"),
        ("vision-mcp-server", "get_active_touches"),
        ("voice-mcp-server", "speak"),
        ("voice-mcp-server", "transcribe"),
        ("voice-mcp-server", "identify_speaker"),
        ("voice-mcp-server", "detect_wake_word"),
        # Read-only listing of the wake words set_wake_word can switch to (which itself has an
        # explicit require_approval rule above) - same reasoning as detect_wake_word.
        ("voice-mcp-server", "list_wake_words"),
        ("voice-mcp-server", "list_voices"),
        # Both read-only: search/fetch a locally-stored offline snapshot, same reasoning as
        # research-mcp-server's search_web/fetch_page above - the CDG's job is to gate what the
        # model does after reading, not the reading itself. See the package README's threat model.
        ("wikipedia-mcp-server", "search_wikipedia"),
        ("wikipedia-mcp-server", "get_wikipedia_article"),
        ("phase4-mcp-server", "propose_change"),
        ("phase4-mcp-server", "get_proposal"),
        ("phase4-mcp-server", "list_proposals"),
        ("phase4-mcp-server", "reject_proposal"),
    }
)

_KNOWN_EXCLUDED_FROM_MODEL = {("ui-bridge-mcp-server", "get_unified_transcript"), ("memory-mcp-server", "list_drafts")}


def _extract_tool_names(source: str) -> list[str]:
    import re

    return re.findall(
        r"@(?:mcp|server)\.tool\(\)\s*\n\s*(?:async\s+)?def\s+(\w+)", source
    )


def _iter_repo_tools():
    """Yields (server_name, tool_name) for every @mcp.tool()/@server.tool() this repo currently
    defines, read directly from each package's server.py source - no package needs to be
    pip-installed for this to see it."""
    for pkg_dir, rel_path in _SERVER_MODULES.items():
        path = PACKAGES_DIR / pkg_dir / rel_path
        if not path.is_file():
            pytest.fail(f"Expected server module missing: {path} (did a package move again?)")
        server_name = _PACKAGE_DIR_TO_SERVER_NAME[pkg_dir]
        for tool_name in _extract_tool_names(path.read_text(encoding="utf-8")):
            yield server_name, tool_name


def test_every_tool_in_the_repo_has_an_explicit_rule_or_is_allowlisted():
    ruleset = load_rules(REPO_RULESET_PATH)
    guard = CoreDirectiveGuard(ruleset)
    uncovered = []
    for server_name, tool_name in _iter_repo_tools():
        decision = guard.evaluate(server_name, tool_name)
        explicitly_ruled = decision.rule_id is not None
        if explicitly_ruled:
            continue
        if (server_name, tool_name) in ALLOWLISTED_DEFAULT_TOOLS:
            continue
        if (server_name, tool_name) in _KNOWN_EXCLUDED_FROM_MODEL:
            continue
        uncovered.append(f"{server_name}.{tool_name}")
    assert not uncovered, (
        "Tool(s) with no CDG rule and not on ALLOWLISTED_DEFAULT_TOOLS - decide whether each "
        f"needs a rule or is genuinely safe, then update cdg_rules.yaml or the allowlist: {uncovered}"
    )


def test_allowlist_does_not_contain_stale_tool_names():
    """The inverse check: catches an allowlist entry left behind after a tool was renamed or
    removed, which would otherwise silently stop meaning anything."""
    real_tools = set(_iter_repo_tools())
    stale = sorted(
        f"{server}.{tool}"
        for server, tool in ALLOWLISTED_DEFAULT_TOOLS
        if (server, tool) not in real_tools
    )
    assert not stale, f"Allowlist entries that no longer match a real tool: {stale}"
