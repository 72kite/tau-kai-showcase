"""Access-tier policy: what an *identified* person is allowed to approve.

Voice identity answers "who is approving"; this package answers "may that person approve
*this* action". It is deliberately separate from the CDG (which decides whether an action needs
approval at all) and from the approval queue (which records the decision) - see
tau_core.access.tiers for the full rationale.
"""

from tau_core.access.tiers import (
    DEFAULT_MIN_TIER,
    DEFAULT_POLICY,
    TierRule,
    load_policy,
    required_tier,
    tier_at_least,
    tier_rank,
)

__all__ = [
    "DEFAULT_MIN_TIER",
    "DEFAULT_POLICY",
    "TierRule",
    "load_policy",
    "required_tier",
    "tier_at_least",
    "tier_rank",
]
