"""Reviewer agents: independent evaluators of proposed changes.

Three agents review each proposal:
1. Security reviewer — checks for vulnerabilities, CDG bypass attempts, etc.
2. Quality reviewer — checks code quality, test coverage, maintainability
3. Intent reviewer — checks if change matches user intent and doesn't exceed scope

Uses Ollama models for evaluation. Falls back to heuristics if Ollama unavailable.
"""

from __future__ import annotations

import json
import os
import logging
from typing import Optional

try:
    from ollama import Client
    HAS_OLLAMA = True
except ImportError:
    HAS_OLLAMA = False

logger = logging.getLogger(__name__)


class ReviewerAgent:
    """Base class for reviewer agents."""

    def __init__(self, reviewer_id: str, role: str):
        self.reviewer_id = reviewer_id
        self.role = role
        self.model_name = os.getenv("REVIEW_MODEL", "qwen2.5:0.5b")
        self.ollama_host = os.getenv("OLLAMA_HOST", "http://localhost:11434")
        self._ollama_client: Optional[Client] = None

    @property
    def ollama_client(self) -> Optional[Client]:
        """Lazy-load Ollama client."""
        if not HAS_OLLAMA:
            return None
        if self._ollama_client is None:
            try:
                self._ollama_client = Client(host=self.ollama_host)
            except Exception as e:
                logger.warning(f"Failed to connect to Ollama at {self.ollama_host}: {e}")
        return self._ollama_client

    def evaluate(self, proposal: dict) -> dict:
        """Evaluate a proposal. Returns {approved, confidence, reasoning}."""
        raise NotImplementedError

    def _evaluate_with_ollama(self, proposal: dict, focus: str) -> Optional[dict]:
        """Evaluate using Ollama LLM. Returns None if unavailable/failed."""
        if not self.ollama_client:
            return None

        prompt = self._build_review_prompt(proposal, focus)
        try:
            response = self.ollama_client.generate(
                model=self.model_name,
                prompt=prompt,
                stream=False,
                temperature=0.3,  # Low temp for consistent reviews
            )
            text = response.get("response", "").strip()
            # Extract JSON from response
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                json_str = text[start:end]
                return json.loads(json_str)
        except Exception as e:
            logger.warning(f"Ollama evaluation failed: {e}")
        return None

    def _build_review_prompt(self, proposal: dict, focus: str) -> str:
        """Build a review prompt focused on a specific aspect."""
        return f"""You are a {self.role} reviewer for an AI system's self-upgrade pipeline.

Proposal: {proposal.get('title')}
Description: {proposal.get('description', '')}

Diff:
{proposal.get('diff', '')}

Rationale: {proposal.get('rationale', '')}

Review focus: {focus}

Evaluate the proposal and respond with ONLY valid JSON (no markdown, no extra text):
{{"approved": true or false, "confidence": float between 0.0 and 1.0, "reasoning": "brief explanation"}}"""


class SecurityReviewer(ReviewerAgent):
    """Reviews proposals for security implications."""

    def __init__(self):
        super().__init__("security", "security engineer")

    def evaluate(self, proposal: dict) -> dict:
        """Check for security vulnerabilities, CDG bypass attempts, etc."""
        llm_result = self._evaluate_with_ollama(
            proposal,
            "Security analysis: Look for vulnerabilities, CDG bypass attempts, privilege escalation, hardcoded credentials, auth bypasses, or dangerous patterns."
        )
        if llm_result:
            return llm_result

        # Fallback to heuristics if Ollama unavailable
        diff = proposal.get("diff", "")
        red_flags = []
        if "CDG" in diff or "cdg_rules" in diff:
            red_flags.append("CDG modification attempted")
        if "bypass" in diff.lower() or "override" in diff.lower():
            red_flags.append("Potential approval bypass detected")
        if "admin" in diff.lower() or "root" in diff.lower():
            red_flags.append("Privilege escalation pattern detected")
        if "password" in diff.lower() or "secret" in diff.lower() or "token" in diff.lower():
            red_flags.append("Possible hardcoded credentials")

        approved = len(red_flags) == 0
        confidence = 0.9 if approved else 0.7

        return {
            "approved": approved,
            "confidence": confidence,
            "reasoning": "; ".join(red_flags) if red_flags else "No security concerns detected (heuristic check)",
        }


class QualityReviewer(ReviewerAgent):
    """Reviews proposals for code quality and maintainability."""

    def __init__(self):
        super().__init__("quality", "code quality engineer")

    def evaluate(self, proposal: dict) -> dict:
        """Check code quality, test coverage, best practices."""
        llm_result = self._evaluate_with_ollama(
            proposal,
            "Code quality analysis: Evaluate test coverage, documentation, error handling, code duplication, readability, and adherence to Python best practices."
        )
        if llm_result:
            return llm_result

        diff = proposal.get("diff", "")
        quality_issues = []

        # Check for test coverage
        if "test_" not in diff and ".py" in diff:
            quality_issues.append("No test additions detected")

        # Check for documentation
        if "def " in diff and '"""' not in diff and "'''" not in diff:
            quality_issues.append("Functions lack docstrings")

        # Check for error handling
        if "def " in diff and "try:" not in diff:
            quality_issues.append("No error handling in new code")

        approved = len(quality_issues) <= 1
        confidence = 0.8

        return {
            "approved": approved,
            "confidence": confidence,
            "reasoning": "; ".join(quality_issues)
            if quality_issues
            else "Code quality looks good (heuristic check)",
        }


class IntentReviewer(ReviewerAgent):
    """Reviews proposals for alignment with user intent and scope creep."""

    def __init__(self):
        super().__init__("intent", "intent alignment analyst")

    def evaluate(self, proposal: dict) -> dict:
        """Check if change matches stated intent and doesn't exceed scope."""
        llm_result = self._evaluate_with_ollama(
            proposal,
            "Intent alignment analysis: Does this change match the stated rationale? Are there scope creeps (changes unrelated to the title/description)? Is the change necessary and sufficient?"
        )
        if llm_result:
            return llm_result

        title = proposal.get("title", "")
        rationale = proposal.get("rationale", "")
        diff = proposal.get("diff", "")

        intent_issues = []

        if len(rationale) < 50:
            intent_issues.append("Rationale too brief; scope unclear")

        # Check for scope creep (files changed not mentioned in title)
        mentioned_in_title = title.lower().split()
        file_count = diff.count("+++")
        if file_count > 5:
            intent_issues.append(f"Many files changed ({file_count}); possible scope creep")

        approved = len(intent_issues) == 0
        confidence = 0.85

        return {
            "approved": approved,
            "confidence": confidence,
            "reasoning": "; ".join(intent_issues)
            if intent_issues
            else "Proposal scope and intent are clear (heuristic check)",
        }


def create_reviewers() -> list[ReviewerAgent]:
    """Create the three independent reviewer agents."""
    return [
        SecurityReviewer(),
        QualityReviewer(),
        IntentReviewer(),
    ]
