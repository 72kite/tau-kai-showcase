from __future__ import annotations

import fnmatch
from enum import Enum
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Effect(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


class Rule(BaseModel):
    id: str
    server: str = "*"
    tool: str = "*"
    effect: Effect
    reason: str

    def matches(self, server: str, tool: str) -> bool:
        return fnmatch.fnmatch(server, self.server) and fnmatch.fnmatch(tool, self.tool)


class RuleSet(BaseModel):
    default_effect: Effect = Effect.ALLOW
    rules: list[Rule] = Field(default_factory=list)


def load_rules(path: str | Path) -> RuleSet:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"CDG ruleset not found at {path}. Tau Core refuses to start without an explicit ruleset."
        )
    data = yaml.safe_load(path.read_text()) or {}
    return RuleSet.model_validate(data)
