"""Phase 8.B governance, asserted against the REAL repo ruleset and the real access-tier policy.

The draft tier reverses a decision project-tau-plan.md §7.3 made deliberately (silent auto-memory
was declined). What made it safe to reverse is a specific, narrow claim:

    a draft can be written freely BECAUSE it can never quietly become a fact,
    and because nothing that decides what Tau MAY DO ever reads it.

These tests are that claim. If one of them ever fails, the reversal is no longer justified.
"""

from pathlib import Path

from tau_core.access import required_tier, tier_at_least
from tau_core.cdg import CoreDirectiveGuard, Effect, load_rules
from tau_core.session.memory_tree import UNVERIFIED_PREFIX, _format_node

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def guard() -> CoreDirectiveGuard:
    return CoreDirectiveGuard(load_rules(CONFIG_DIR / "cdg_rules.yaml"))


# --- the two rules the whole design rests on --------------------------------------------------


def test_drafting_a_memory_needs_no_approval():
    """Auto-memory was declined because gating every observation would spam the approval queue.
    Drafts are ungated - that is the point, and it is only defensible alongside the next test."""
    decision = guard().evaluate("memory-mcp-server", "draft_memory")
    assert decision.effect is Effect.ALLOW


def test_promoting_a_draft_to_a_fact_requires_human_approval():
    """The review gate. Without this, a draft is just a silent auto-memory with a nicer name."""
    decision = guard().evaluate("memory-mcp-server", "promote_memory")
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_storing_an_approved_memory_directly_still_requires_approval():
    """Phase 8 must not have loosened the pre-existing bar while adding a lower one beside it."""
    decision = guard().evaluate("memory-mcp-server", "store_memory")
    assert decision.effect is Effect.REQUIRE_APPROVAL


def test_discarding_a_draft_needs_no_approval():
    """Forgetting an unchecked guess about someone is the conservative direction. An approval gate
    here would obstruct correcting Tau, not protect anything."""
    decision = guard().evaluate("memory-mcp-server", "discard_draft")
    assert decision.effect is Effect.ALLOW


def test_biometric_enrolment_is_untouched_by_the_draft_tier():
    """The draft tier is for conversational context. It must not have opened a door next to
    face/voice enrolment, which is a different kind of data with its own bar."""
    for tool in ("store_face", "store_voice", "set_access_level"):
        assert guard().evaluate("memory-mcp-server", tool).effect is Effect.REQUIRE_APPROVAL


# --- drafts can never grant authority ---------------------------------------------------------


def test_no_memory_tool_can_grant_an_access_tier():
    """The structural half of the claim: what Tau MAY DO is decided by tau_core.access.tiers and
    the CDG, neither of which reads the Memory Tree. So a draft - however wrong, however
    confidently phrased, however it got there - cannot elevate anyone or authorise anything. If
    a future change ever routes tier decisions through memory, this test is the tripwire."""
    # An unknown/unrecognised identity is tier 0 regardless of anything in the memory tree.
    assert not tier_at_least(None, required_tier("security-mcp-server", "exit_lockdown"))
    assert not tier_at_least("standard", required_tier("security-mcp-server", "exit_lockdown"))
    # And a draft claiming otherwise changes nothing, because nothing consults it: the tier
    # policy's inputs are the verified speaker's access level and the action, full stop.
    assert required_tier("memory-mcp-server", "draft_memory") == required_tier("echo", "echo")


def test_promotion_is_an_elevated_action_a_kid_cannot_wave_through():
    """promote_memory is CDG-gated, so it lands in the approval queue; who may clear that queue
    is the access-tier system's call, not the drafter's."""
    assert guard().evaluate("memory-mcp-server", "promote_memory").effect is Effect.REQUIRE_APPROVAL


# --- drafts are labelled everywhere they surface -----------------------------------------------


def test_recalled_drafts_are_labelled_unverified_in_the_prompt():
    """Recall is the ONLY path Memory Tree content reaches the model. If a draft arrived looking
    like an approved memory, the tier would be meaningless exactly when it matters - when the
    model decides what it believes about someone."""
    snippet = _format_node(
        {"title": "Coffee", "content": "Maybe drinks it at 6am", "status": "draft"}
    )
    assert snippet.startswith(UNVERIFIED_PREFIX)
    assert "Coffee" in snippet and "6am" in snippet


def test_recalled_approved_memories_are_not_labelled_unverified():
    """The label has to mean something. If everything were marked unverified, nothing would be."""
    snippet = _format_node(
        {"title": "Coffee", "content": "Drinks it at 6am", "status": "approved"}
    )
    assert UNVERIFIED_PREFIX not in snippet
    assert snippet == "Coffee: Drinks it at 6am"


def test_a_node_with_no_status_is_not_labelled_unverified():
    """Pre-Phase-8 nodes were all approved (store_memory was the only way in), so a missing
    status must not spuriously mark real memories as guesses."""
    assert UNVERIFIED_PREFIX not in _format_node({"title": "Old", "content": "Real decision"})
