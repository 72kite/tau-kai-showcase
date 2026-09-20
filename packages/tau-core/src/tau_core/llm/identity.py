"""Tau's deterministic answer to "what are you" and "who made you".

Split out of agent.py in Phase 35. A pure function with no coupling to TauAssistant state, which
is exactly why it belongs on its own: the shortcut runs before any model or tool does, and it is
easier to reason about (and to test) when it isn't buried among the turn pipeline.
"""

from __future__ import annotations

import re

# --- Deterministic identity answer (Phase 16) -------------------------------------------------
#
# Tau's name and creator are fixed facts, not something to ask a model - and definitely not the
# web. On this hardware the model + 7b router demonstrably could not be trusted with them: asked
# "who created you" it web-searched and returned Warhammer lore; asked its name it answered
# "Claude", then leaked "Anthropic" as its maker. The system prompt (prompts.py) states the fact, but
# the router still routes the question to search_web, so the prompt alone can't honour "answer
# WITHOUT calling research". So identity/creator questions are short-circuited here, before any
# model or tool runs: a fixed, correct, instant answer. Model/hardware questions ("which model do
# you run on") are deliberately NOT caught - those are dynamic and still go to
# utility-mcp-server__get_system_status.
CREATOR_NAME = "Zion Kinniebrew"
_IDENTITY_ANSWER = f"I'm Tau, this home's own AI assistant. I was created by {CREATOR_NAME}."
_IDENTITY_DENIAL = f"No — I'm Tau, this home's own AI assistant, created by {CREATOR_NAME}."

# Names of other assistants/companies a user might accuse Tau of being. Kept off the model path.
_OTHER_AI = (
    r"(?:claude|chat\s?-?gpt|gpt-?\d*|open\s?ai|anthropic|gemini|bard|"
    r"llama|meta\s?ai|qwen|alibaba|mistral|copilot|grok|deep\s?seek)"
)
# "you"-anchored so "who created the automation?" does NOT match - only self-referential identity.
_IDENTITY_RE = re.compile(
    r"\b(?:"
    r"who(?:'?s| is| are)\s+you(?:r)?(?:\s+(?:creator|maker|developer|owner|author|inventor))?"
    r"|who\s+(?:created|made|built|designed|developed|invented|programmed|coded|trained|owns?)\s+you"
    r"|what(?:'?s| is|\s+are)?\s+(?:your\s+name|you\s+called)"
    r"|who\s+do\s+you\s+work\s+for"
    r")\b",
    re.IGNORECASE,
)
# Accusations to deny: "are you claude", "you're chatgpt", "admit you are openai".
_IDENTITY_DENIAL_RE = re.compile(
    rf"\b(?:are\s+you|aren'?t\s+you|you(?:'re| are)|admit\s+(?:you(?:'re| are)?|that\s+you(?:'re| are)?))"
    rf"\s+(?:really\s+|actually\s+|just\s+|secretly\s+|a\s+|an\s+)*{_OTHER_AI}",
    re.IGNORECASE,
)


def identity_shortcut_answer(user_text: str) -> str | None:
    """A fixed answer for a question about Tau's own name/creator (or an accusation of being
    another AI), or None if the message isn't one. Deterministic and tool-free by design - see the
    block comment above. English only, matching the default reply language; callers running a
    non-English deployment should skip this and let the model answer."""
    if _IDENTITY_DENIAL_RE.search(user_text):
        return _IDENTITY_DENIAL
    if _IDENTITY_RE.search(user_text):
        return _IDENTITY_ANSWER
    return None
