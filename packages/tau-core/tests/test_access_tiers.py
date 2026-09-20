"""Access-tier policy: the pure ranking/matching logic that decides whether an identified
person may approve a given action. The web-bridge wiring is covered in test_voice_identity.py;
this file pins the policy semantics themselves."""

from tau_core.access import (
    DEFAULT_MIN_TIER,
    TierRule,
    required_tier,
    tier_at_least,
    tier_rank,
)


def test_unknown_and_unrecognized_levels_rank_lowest():
    assert tier_rank(None) == 0
    assert tier_rank("") == 0
    assert tier_rank("unknown") == 0
    assert tier_rank("something-nobody-defined") == 0  # fail-safe, not an error


def test_aliases_collapse_onto_canonical_ranks():
    assert tier_rank("kid") == tier_rank("guest") == 1
    assert tier_rank("owner") == tier_rank("admin") == 3
    assert tier_rank("ADMIN") == tier_rank(" admin ") == 3  # case/space-insensitive


def test_tier_at_least_is_inclusive_and_ordered():
    assert tier_at_least("admin", "admin")
    assert tier_at_least("admin", "kid")
    assert not tier_at_least("kid", "admin")
    # Missing/unknown never satisfies a gate above baseline.
    assert not tier_at_least(None, "guest")
    assert tier_at_least("guest", DEFAULT_MIN_TIER)  # baseline is satisfiable by any identity


def test_elevated_actions_require_admin():
    assert required_tier("security-mcp-server", "exit_lockdown") == "admin"
    assert required_tier("memory-mcp-server", "set_access_level") == "admin"
    assert required_tier("phase4-mcp-server", "user_override_proposal") == "admin"


def test_unlisted_actions_fall_through_to_baseline():
    assert required_tier("fabrication-mcp-server", "submit_print_job") == DEFAULT_MIN_TIER
    assert required_tier("home-assistant-mcp-server", "call_security_service") == DEFAULT_MIN_TIER
    assert required_tier("anything", "else") == DEFAULT_MIN_TIER


def test_custom_policy_supports_globs_first_match_wins():
    policy = (
        TierRule("proxmox-mcp-server", "*", "admin", "all proxmox writes admin-only"),
        TierRule("*", "*", "standard", "everything else needs standard"),
    )
    assert required_tier("proxmox-mcp-server", "create_lxc", policy) == "admin"
    assert required_tier("voice-mcp-server", "speak", policy) == "standard"
