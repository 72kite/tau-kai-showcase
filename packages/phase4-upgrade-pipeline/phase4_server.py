"""Phase 4 MCP server: governed self-upgrade pipeline.

Exposes tools for proposing changes, reviewing proposals, and merging approved changes.
Integrates with three independent reviewer agents (Security, Quality, Intent).
"""

import json
import os
from mcp.server.fastmcp import FastMCP

from proposal_store import ProposalStore
from reviewer_agents import create_reviewers

server = FastMCP(
    "phase4-upgrade",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Monkeypatch seam for tests
_proposal_store = None
_reviewers = None


def _store() -> ProposalStore:
    global _proposal_store
    if _proposal_store is None:
        db_path = os.getenv("PROPOSAL_DB_PATH", "./data/proposals.json")
        _proposal_store = ProposalStore(db_path)
    return _proposal_store


def _get_reviewers():
    global _reviewers
    if _reviewers is None:
        _reviewers = create_reviewers()
    return _reviewers


@server.tool()
def propose_change(title: str, diff: str, rationale: str) -> str:
    """Propose a system change for review and approval.

    title: brief description of the change
    diff: unified diff format showing changes
    rationale: explanation of why this change is needed

    Returns proposal_id and triggers automatic review by three agents.
    """
    store = _store()
    reviewers = _get_reviewers()

    proposal = store.create_proposal(title, "", diff, rationale)

    for reviewer in reviewers:
        review_result = reviewer.evaluate(
            {
                "title": title,
                "diff": diff,
                "rationale": rationale,
            }
        )
        store.add_review(
            proposal.id,
            reviewer.reviewer_id,
            review_result["approved"],
            review_result["confidence"],
            review_result["reasoning"],
        )

    # Fetch updated proposal with reviews
    proposal = store.get_proposal(proposal.id)

    return json.dumps(
        {
            "proposal_id": proposal.id,
            "title": proposal.title,
            "status": proposal.status,
            "reviews": [
                {
                    "reviewer": r.reviewer_id,
                    "approved": r.approved,
                    "confidence": r.confidence,
                    "reasoning": r.reasoning,
                }
                for r in proposal.reviews
            ],
            "approval_status": proposal.approval_status(),
        }
    )


@server.tool()
def get_proposal(proposal_id: str) -> str:
    """Get full details of a proposal including all reviews."""
    store = _store()
    proposal = store.get_proposal(proposal_id)

    if not proposal:
        raise ValueError(f"Proposal {proposal_id} not found")

    return json.dumps(
        {
            "proposal_id": proposal.id,
            "title": proposal.title,
            "description": proposal.description,
            "status": proposal.status,
            "proposed_at": proposal.proposed_at,
            "reviews": [
                {
                    "reviewer": r.reviewer_id,
                    "approved": r.approved,
                    "confidence": r.confidence,
                    "reasoning": r.reasoning,
                    "timestamp": r.timestamp,
                }
                for r in proposal.reviews
            ],
            "approval_status": proposal.approval_status(),
            "ready_to_merge": proposal.is_ready_to_merge(),
            "merged_at": proposal.merged_at,
        }
    )


@server.tool()
def list_proposals(status: str = "") -> str:
    """List all proposals, optionally filtered by status.

    status options: pending_review, pending_user_decision, approved, rejected, merged
    """
    store = _store()
    proposals = store.list_proposals(status if status else None)

    return json.dumps(
        {
            "proposals": [
                {
                    "proposal_id": p.id,
                    "title": p.title,
                    "status": p.status,
                    "proposed_at": p.proposed_at,
                    "review_count": len(p.reviews),
                    "approval_status": p.approval_status(),
                }
                for p in proposals
            ],
            "total": len(proposals),
        }
    )


@server.tool()
def user_override_proposal(proposal_id: str, override_reason: str = "") -> str:
    """User overrides review process and approves a proposal.

    This allows merging proposals that didn't achieve unanimous approval.
    Requires explicit user authorization (gated by CDG).
    """
    store = _store()
    proposal = store.approve_with_override(proposal_id)

    return json.dumps(
        {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "override_used": True,
            "override_reason": override_reason,
            "ready_to_merge": proposal.is_ready_to_merge(),
        }
    )


@server.tool()
def merge_proposal(proposal_id: str) -> str:
    """Mark a proposal as merged (after CI/CD passes).

    Proposal must have unanimous approval or user override to be mergeable.
    This is called after CI/CD pipeline completes successfully.
    """
    store = _store()

    try:
        proposal = store.merge_proposal(proposal_id)
        return json.dumps(
            {
                "proposal_id": proposal.id,
                "status": proposal.status,
                "merged_at": proposal.merged_at,
            }
        )
    except ValueError as e:
        raise RuntimeError(f"Cannot merge: {str(e)}")


@server.tool()
def reject_proposal(proposal_id: str, reason: str = "") -> str:
    """Reject a proposal (e.g., if CI/CD fails or user denies it)."""
    store = _store()
    proposal = store.reject_proposal(proposal_id)

    return json.dumps(
        {
            "proposal_id": proposal.id,
            "status": proposal.status,
            "rejection_reason": reason,
        }
    )


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
