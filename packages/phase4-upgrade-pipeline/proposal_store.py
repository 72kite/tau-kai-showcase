"""Proposal store: persistent queue of proposed changes awaiting review and merge."""

import uuid
from pathlib import Path
from datetime import datetime
from dataclasses import asdict, dataclass, field
from typing import Optional

import crypto_store


@dataclass
class Review:
    """Individual reviewer's assessment of a proposal."""
    reviewer_id: str  # e.g., "security", "quality", "intent"
    approved: bool
    confidence: float  # 0-1
    reasoning: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class Proposal:
    """A proposed change awaiting review and approval."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    title: str = ""
    description: str = ""
    diff: str = ""  # unified diff format
    rationale: str = ""
    proposed_by: str = "tau"
    proposed_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    reviews: list[Review] = field(default_factory=list)
    status: str = "pending_review"  # pending_review, approved, rejected, merged
    approval_override_used: bool = False
    merged_at: Optional[str] = None

    def add_review(self, reviewer_id: str, approved: bool, confidence: float, reasoning: str):
        """Add a reviewer's assessment."""
        self.reviews.append(Review(reviewer_id, approved, confidence, reasoning))

    def approval_status(self) -> dict:
        """Get approval summary: unanimous, majority, split, etc."""
        if not self.reviews:
            return {"status": "no_reviews", "approved_count": 0, "total": 0}

        approved_count = sum(1 for r in self.reviews if r.approved)
        total = len(self.reviews)

        if approved_count == total:
            return {"status": "unanimous", "approved_count": approved_count, "total": total}
        elif approved_count > total / 2:
            return {"status": "majority", "approved_count": approved_count, "total": total}
        elif approved_count > 0:
            return {"status": "split", "approved_count": approved_count, "total": total}
        else:
            return {"status": "rejected", "approved_count": 0, "total": total}

    def is_ready_to_merge(self) -> bool:
        """Check if proposal can be merged (unanimous approval or user override)."""
        status = self.approval_status()
        return status["status"] == "unanimous" or self.approval_override_used


class ProposalStore:
    """Persistent proposal queue backed by JSON."""

    def __init__(self, db_path: str = "./data/proposals.json"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Encrypted at rest (Phase 10) when TAU_MASTER_KEY is set; plaintext, unchanged, if unset.
        self._key = crypto_store.resolve_key(self.db_path)
        self._proposals = self._load()

    def _load(self) -> dict[str, Proposal]:
        """Load proposals from disk."""
        if self.db_path.exists():
            data = crypto_store.read_json(self.db_path, self._key)
            return {
                pid: Proposal(**pdata) for pid, pdata in data.items()
            }
        return {}

    def _save(self):
        """Save proposals to disk."""
        data = {
            pid: asdict(proposal)
            for pid, proposal in self._proposals.items()
        }
        self.db_path.write_bytes(crypto_store.write_json_bytes(data, self._key))

    def create_proposal(
        self,
        title: str,
        description: str,
        diff: str,
        rationale: str,
    ) -> Proposal:
        """Create a new proposal."""
        proposal = Proposal(
            title=title,
            description=description,
            diff=diff,
            rationale=rationale,
        )
        self._proposals[proposal.id] = proposal
        self._save()
        return proposal

    def get_proposal(self, proposal_id: str) -> Optional[Proposal]:
        """Fetch a proposal by ID."""
        return self._proposals.get(proposal_id)

    def list_proposals(self, status: Optional[str] = None) -> list[Proposal]:
        """List all proposals, optionally filtered by status."""
        proposals = list(self._proposals.values())
        if status:
            proposals = [p for p in proposals if p.status == status]
        return proposals

    def add_review(
        self,
        proposal_id: str,
        reviewer_id: str,
        approved: bool,
        confidence: float,
        reasoning: str,
    ) -> Proposal:
        """Add a review to a proposal."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        proposal.add_review(reviewer_id, approved, confidence, reasoning)

        # Auto-update status if all reviewers have voted
        if len(proposal.reviews) >= 3:  # Assume 3 reviewers
            status = proposal.approval_status()
            if status["status"] == "unanimous":
                proposal.status = "approved"
            elif status["status"] in ["rejected", "split", "majority"]:
                proposal.status = "pending_user_decision"

        self._save()
        return proposal

    def approve_with_override(self, proposal_id: str) -> Proposal:
        """User overrides review process (approval_override_used = True)."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        proposal.approval_override_used = True
        proposal.status = "approved"
        self._save()
        return proposal

    def merge_proposal(self, proposal_id: str) -> Proposal:
        """Mark a proposal as merged (e.g., after CI/CD completes)."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        if not proposal.is_ready_to_merge():
            raise ValueError(
                f"Proposal {proposal_id} is not ready to merge (needs unanimous approval or user override)"
            )

        proposal.status = "merged"
        proposal.merged_at = datetime.utcnow().isoformat()
        self._save()
        return proposal

    def reject_proposal(self, proposal_id: str) -> Proposal:
        """Reject a proposal."""
        proposal = self._proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal {proposal_id} not found")

        proposal.status = "rejected"
        self._save()
        return proposal
