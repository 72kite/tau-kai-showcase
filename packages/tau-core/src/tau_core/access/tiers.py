"""Access-tier policy for approvals.

Once a voice challenge has *identified* who is trying to approve an action (see
tau_core.web.identity), this module answers the separate question: is that person's access
level high enough to approve *this particular* action? "Kids can't approve lockdown exits"
(project-tau-plan.md) lives here, as code, not as prompt text.

Design choices, and why:

- **Fail-safe ranking.** Levels are ranked low->high; any unrecognized or missing level ranks
  0 ("unknown"), so a gate above baseline is never satisfied by accident. This mirrors the rest
  of the voice pipeline, which always fails toward "unknown = lowest access, never false accept."

- **Only elevated actions are listed.** Anything not matched by a rule requires DEFAULT_MIN_TIER
  ("unknown"), i.e. no tier beyond a verified identity. Enabling voice approval therefore never
  silently locks out an enrolled-but-unclassified person for routine approvals - it only gates
  the high-consequence actions in DEFAULT_POLICY. (Freshly enrolled people default to access
  level "unknown" until an admin sets one via memory-mcp-server.set_access_level, which is
  itself an admin-tier action here - so the tier system can't be escalated from below.)

- **Separate from the CDG.** The CDG decides *whether* an action needs approval; this decides
  *who* may give it. Keeping them apart means the CDG ruleset (immutable, out-of-band-only) is
  never touched to add a person or retune a tier.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from pathlib import Path

# Canonical ranks, low -> high. Aliases collapse onto a rank so a household can name tiers
# naturally ("kid", "owner") without editing this table. Anything not here ranks 0.
_TIER_RANK: dict[str, int] = {
    "unknown": 0,
    "guest": 1,
    "kid": 1,
    "child": 1,
    "visitor": 1,
    "standard": 2,
    "member": 2,
    "resident": 2,
    "adult": 2,
    "user": 2,
    "admin": 3,
    "owner": 3,
    "root": 3,
}

# Unlisted actions need only a verified identity (this rank-0 level), not a higher tier.
DEFAULT_MIN_TIER = "unknown"


def tier_rank(level: str | None) -> int:
    """Rank of an access level; unknown/blank/unrecognized -> 0 (fail-safe)."""
    if not level:
        return 0
    return _TIER_RANK.get(level.strip().lower(), 0)


def tier_at_least(have: str | None, need: str | None) -> bool:
    """True if `have` meets or exceeds `need` on the tier ladder."""
    return tier_rank(have) >= tier_rank(need)


@dataclass(frozen=True)
class TierRule:
    """A minimum access tier required to APPROVE calls matching (server, tool). fnmatch globs,
    same style as the CDG ruleset, first match wins."""

    server: str
    tool: str
    min_tier: str
    reason: str = ""


# The high-consequence actions that need more than a verified identity to approve. These names
# intentionally track the require_approval rules in config/cdg_rules.yaml - the CDG makes them
# need approval; this makes that approval admin-only.
DEFAULT_POLICY: tuple[TierRule, ...] = (
    TierRule(
        "security-mcp-server", "exit_lockdown", "admin",
        "Clearing lockdown after a detected intrusion is admin-only - the plan's canonical "
        "'kids can't approve lockdown exits' rule.",
    ),
    TierRule(
        "memory-mcp-server", "set_access_level", "admin",
        "Changing a person's access level is the tier system's own control surface; only an "
        "admin may approve it, so tiers can't be escalated from below.",
    ),
    TierRule(
        "phase4-mcp-server", "user_override_proposal", "admin",
        "Overriding the self-upgrade review process (bypassing unanimous agent review) is "
        "admin-only.",
    ),
    TierRule(
        "proxmox-mcp-server", "create_lxc", "admin",
        "Creating infrastructure is admin-only.",
    ),
    TierRule(
        "proxmox-mcp-server", "apply_update", "admin",
        "Applying node updates (possible reboots) is admin-only.",
    ),
    TierRule(
        "proxmox-mcp-server", "restart_service", "admin",
        "Restarting Proxmox services is admin-only.",
    ),
)


def required_tier(server: str, tool: str, policy: tuple[TierRule, ...] = DEFAULT_POLICY) -> str:
    """Minimum access tier to approve (server, tool). First matching rule wins; unmatched ->
    DEFAULT_MIN_TIER (any verified identity)."""
    for rule in policy:
        if fnmatch.fnmatch(server, rule.server) and fnmatch.fnmatch(tool, rule.tool):
            return rule.min_tier
    return DEFAULT_MIN_TIER


def load_policy(path: str | Path) -> tuple[TierRule, ...]:
    """Load an access-tier policy from YAML, for overriding DEFAULT_POLICY without code changes.

    Format mirrors cdg_rules.yaml:

        rules:
          - server: "security-mcp-server"
            tool: "exit_lockdown"
            min_tier: "admin"
            reason: "..."
    """
    import yaml

    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    rules = []
    for raw in data.get("rules", []):
        rules.append(
            TierRule(
                server=raw["server"],
                tool=raw["tool"],
                min_tier=raw["min_tier"],
                reason=raw.get("reason", ""),
            )
        )
    return tuple(rules)
