"""Tests for Phase 4 upgrade pipeline."""

import json
import tempfile
from pathlib import Path

import pytest

import crypto_store
from proposal_store import ProposalStore, Proposal
from reviewer_agents import create_reviewers, SecurityReviewer, QualityReviewer, IntentReviewer


def test_proposal_store_create():
    """ProposalStore can create and retrieve proposals."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ProposalStore(str(Path(tmpdir) / "proposals.json"))

        proposal = store.create_proposal(
            title="Add logging",
            description="Improve observability",
            diff="--- a/app.py\n+++ b/app.py\n+import logging",
            rationale="Better debugging",
        )

        assert proposal.id
        assert proposal.title == "Add logging"
        assert proposal.status == "pending_review"

        retrieved = store.get_proposal(proposal.id)
        assert retrieved.id == proposal.id


def test_proposal_store_persists():
    """Proposals persist across ProposalStore instances."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "proposals.json")

        store1 = ProposalStore(db_path)
        proposal1 = store1.create_proposal(
            title="Test", description="", diff="test", rationale="test"
        )

        store2 = ProposalStore(db_path)
        proposal2 = store2.get_proposal(proposal1.id)

        assert proposal2.title == "Test"


def test_proposal_approval_status():
    """Proposal approval status correctly reflects reviews."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ProposalStore(str(Path(tmpdir) / "proposals.json"))
        proposal = store.create_proposal("Test", "", "diff", "rationale")

        # No reviews yet
        assert proposal.approval_status()["status"] == "no_reviews"

        # One approval (100% approval so far = unanimous at this point)
        proposal = store.add_review(proposal.id, "reviewer1", True, 0.9, "Looks good")
        assert proposal.approval_status()["status"] == "unanimous"  # 1/1 = 100%

        # Two approvals (still 100%)
        proposal = store.add_review(proposal.id, "reviewer2", True, 0.85, "Ok")
        assert proposal.approval_status()["status"] == "unanimous"  # 2/2 = 100%

        # Three approvals (unanimous, and auto-approved when 3 reviewers vote)
        proposal = store.add_review(proposal.id, "reviewer3", True, 0.8, "Approved")
        assert proposal.approval_status()["status"] == "unanimous"
        assert proposal.status == "approved"


def test_proposal_split_decision():
    """Proposal with split reviews is majority, not unanimous."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ProposalStore(str(Path(tmpdir) / "proposals.json"))
        proposal = store.create_proposal("Test", "", "diff", "rationale")

        store.add_review(proposal.id, "reviewer1", True, 0.9, "Good")
        store.add_review(proposal.id, "reviewer2", False, 0.7, "Concerns")
        proposal = store.add_review(proposal.id, "reviewer3", True, 0.8, "Approve")

        # 2 approved, 1 rejected = majority (not unanimous)
        assert proposal.approval_status()["status"] == "majority"
        # Majority doesn't auto-approve, stays pending
        assert proposal.status == "pending_user_decision"


def test_user_override():
    """User can override non-unanimous decisions."""
    with tempfile.TemporaryDirectory() as tmpdir:
        store = ProposalStore(str(Path(tmpdir) / "proposals.json"))
        proposal = store.create_proposal("Test", "", "diff", "rationale")

        store.add_review(proposal.id, "reviewer1", True, 0.9, "Good")
        store.add_review(proposal.id, "reviewer2", False, 0.7, "Concerns")
        store.add_review(proposal.id, "reviewer3", True, 0.8, "Approve")

        assert proposal.is_ready_to_merge() is False

        proposal = store.approve_with_override(proposal.id)
        assert proposal.approval_override_used is True
        assert proposal.is_ready_to_merge() is True


def test_security_reviewer():
    """SecurityReviewer detects red flags."""
    reviewer = SecurityReviewer()

    # Clean proposal
    result = reviewer.evaluate(
        {"title": "Add logging", "diff": "diff", "rationale": "Better debugging"}
    )
    assert result["approved"] is True

    # CDG modification attempt
    result = reviewer.evaluate(
        {"title": "Update CDG", "diff": "cdg_rules.yaml", "rationale": "bypass"}
    )
    assert result["approved"] is False


def test_quality_reviewer():
    """QualityReviewer checks for test coverage."""
    reviewer = QualityReviewer()

    # No tests
    result = reviewer.evaluate(
        {"title": "Add function", "diff": "def new_func():\n    return 42", "rationale": "New feature"}
    )
    assert result["approved"] is False

    # With tests
    result = reviewer.evaluate(
        {
            "title": "Add function",
            "diff": 'def new_func():\n    """docstring"""\n    return 42',
            "rationale": "New feature",
        }
    )
    assert result["approved"] is True


def test_intent_reviewer():
    """IntentReviewer checks scope alignment."""
    reviewer = IntentReviewer()

    # Clear scope
    result = reviewer.evaluate(
        {
            "title": "Add logging",
            "diff": "diff",
            "rationale": "Implement comprehensive logging for better observability and debugging",
        }
    )
    assert result["approved"] is True

    # Unclear scope
    result = reviewer.evaluate(
        {"title": "Update", "diff": "diff", "rationale": "fix"}
    )
    assert result["approved"] is False


# --- Phase 10: encryption at rest --------------------------------------------------------------


def test_proposal_store_stays_plaintext_without_a_master_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TAU_MASTER_KEY", raising=False)
    db_path = tmp_path / "proposals.json"
    store = ProposalStore(str(db_path))
    store.create_proposal(title="Add logging", description="", diff="d", rationale="r")

    raw = db_path.read_bytes()
    assert not raw.startswith(crypto_store._MAGIC)
    assert b"Add logging" in raw


def test_proposal_store_encrypts_when_master_key_set_and_round_trips(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "correct horse battery staple")
    db_path = tmp_path / "proposals.json"

    store1 = ProposalStore(str(db_path))
    proposal = store1.create_proposal(title="Add logging", description="", diff="d", rationale="r")

    raw = db_path.read_bytes()
    assert raw.startswith(crypto_store._MAGIC)
    assert b"Add logging" not in raw

    store2 = ProposalStore(str(db_path))
    assert store2.get_proposal(proposal.id).title == "Add logging"


def test_proposal_store_wrong_master_key_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("TAU_MASTER_KEY", "right passphrase")
    db_path = tmp_path / "proposals.json"
    store = ProposalStore(str(db_path))
    store.create_proposal(title="Test", description="", diff="d", rationale="r")

    monkeypatch.setenv("TAU_MASTER_KEY", "wrong passphrase")
    with pytest.raises(ValueError):
        ProposalStore(str(db_path))


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
