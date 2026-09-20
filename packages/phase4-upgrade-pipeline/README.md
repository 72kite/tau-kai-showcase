# Phase 4: Governed Self-Upgrade Pipeline

Implements Tau's self-upgrade governance: propose changes, independent review, approval gates, CI/CD merge.

## Architecture

Three independent reviewer agents evaluate every proposal:
1. **Security Reviewer** — checks for CDG bypass attempts, privilege escalation, vulnerabilities
2. **Quality Reviewer** — checks code quality, test coverage, documentation
3. **Intent Reviewer** — checks scope alignment and prevents creep

Approval rules:
- **Unanimous required**: all 3 reviewers approve → auto-merge after CI/CD passes
- **Not unanimous**: proposal enters `pending_user_decision` state → user can override
- **CDG ruleset never touched**: proposals cannot modify `tau-core/config/cdg_rules.yaml`

## Workflow

```
Tau proposes change
    ↓
↳ create_proposal(title, diff, rationale)
    ↓
Three reviewers independently evaluate
    ├─ Security review (auto-run, stub heuristics or LLM)
    ├─ Quality review (auto-run, stub heuristics or LLM)
    └─ Intent review (auto-run, stub heuristics or LLM)
    ↓
Approval logic
    ├─ Unanimous → proposal.status = "approved"
    ├─ Not unanimous → proposal.status = "pending_user_decision"
    └─ User can override: user_override_proposal(proposal_id) → "approved"
    ↓
CI/CD merge (separate infrastructure)
    ├─ Run tests on proposed changes
    ├─ Build and validate
    └─ merge_proposal(proposal_id) → "merged"
```

## Files

- `proposal_store.py`: JSON-backed proposal queue with Review records
- `reviewer_agents.py`: Three independent reviewer classes (Security, Quality, Intent)
- `phase4_server.py` (to build): MCP server exposing tools

## Tools (to expose via MCP)

- `propose_change(title, diff, rationale)` — Tau proposes a change
- `get_proposal(proposal_id)` — Fetch proposal + review status
- `list_proposals(status)` — List pending/approved/merged proposals
- `user_override_proposal(proposal_id)` — User bypasses review (requires auth)

## Safety invariants

1. **CDG ruleset immutable**: no proposal can modify `tau-core/config/cdg_rules.yaml`
2. **Unanimous or user consent**: changes require either unanimous approval OR user override
3. **Three independent reviewers**: reduces bias, increases scrutiny
4. **CI/CD gate**: changes only merge after tests pass
5. **Audit trail**: all proposals, reviews, and merges are logged

## Next steps

1. Wire up reviewer agent LLM calls (currently stubs using heuristics)
2. Build Phase 4 MCP server and register in tau-core
3. Integrate with CI/CD pipeline (GitHub Actions, GitLab CI, etc.)
4. Test full workflow: propose → review → merge
