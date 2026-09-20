from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic_ai import Agent
from pydantic_ai.models import Model

logger = logging.getLogger(__name__)

# Phase 18 - the tool-need gate.
#
# Every chat turn used to hand the main model all ~77 tool schemas, even a pure general-knowledge
# question ("what's the capital of France", "explain photosynthesis"). Two costs: the model
# sometimes answered a general question by SPURIOUSLY calling a tool (web-searching a fact it knew,
# calling get_system_status for "what can you do"), and the reply quality suffered from a prompt
# bloated with tool-selection machinery it didn't need. This gate decides, before the expensive
# call, whether a turn should see the toolset at all. Turns that don't take a lean, tool-free path
# (see agent.GENERAL_SYSTEM_PROMPT).
#
# THE SAFETY CONTRACT, and why the gate is asymmetric: a false "needs tools" only costs latency
# (the full path still answers correctly). A false "tool-free" is dangerous - it makes Tau
# hallucinate live state ("are the lights on?" -> "yes", without ever checking). So every decision
# here biases hard toward the full path:
#   - the heuristic's trigger list is deliberately broad (any home/system/time/web/memory-write
#     cue -> tools), and over-matching a general question just makes it slower, never wrong;
#   - the classifier only diverts to the fast path on an explicit, confident "DIRECT", and
#     defaults to "tools" on any error, timeout, or unparseable reply.

Verdict = Literal["tools", "no_tools", "uncertain"]

# Words/phrases that strongly imply the turn concerns real home/system state, the current
# time/date, Tau's own hardware/inventory, a live web lookup, or writing a memory - i.e. something
# only a tool can answer truthfully. Matching ANY of these forces the full toolset path. The list
# errs broad on purpose (see the safety contract above): a general question that happens to contain
# one of these words just pays for the full path, which still answers it fine.
#
# **Keyed by server since Phase 23**, which is a refactor and not a rewrite: this was one flat list
# whose entries were already grouped by server *in comments*. Promoting those comments to real keys
# costs nothing, keeps one source of truth, and is what lets the same vocabulary answer a second,
# sharper question - not just "does this turn need tools?" but "*which servers* does it need?" (see
# heuristic_servers_needed). The union of these groups is exactly the old flat list, and
# test_server_scope.py asserts that equality so the two questions can never drift apart.
SERVER_TRIGGER_WORDS: dict[str, list[str]] = {
    "home-assistant-mcp-server": [
        r"light", r"lamp", r"switch", r"plug", r"outlet", r"thermostat", r"temperature",
        r"heater", r"heating", r"cooling", r"air ?condition", r"\bfan\b", r"\block\b", r"unlock",
        r"locked", r"\bdoor", r"garage", r"alarm", r"\barm(ed|s)?\b", r"disarm", r"\bsensor",
        r"motion", r"blind", r"shade", r"\bdim\b", r"brighten", r"brightness", r"\bscene\b",
    ],
    "proxmox-mcp-server": [
        r"\bvm\b", r"\bvms\b", r"container", r"\blxc\b", r"proxmox", r"cluster", r"\bnode\b",
        r"\bcve\b", r"\bpatch(es|ed)?\b", r"disk usage", r"cpu load",
    ],
    "vision-mcp-server": [
        r"camera", r"snapshot", r"webcam", r"\bptz\b", r"doorbell", r"who'?s? (at|there)",
        r"look outside",
    ],
    "security-mcp-server": [
        r"lockdown", r"intrud", r"intrusion", r"breach", r"\bsecure\b", r"security",
        # Added Phase 23: the words people actually use for the event. "Has there been a break-in
        # today?" matched NONE of the six above - it matched utility's "today" instead, and scoped
        # the turn to a roster with no security server in it at all. Found by measuring scoping
        # against the eval prompts rather than by reading the list.
        r"break[- ]?in", r"burglar", r"burgle", r"\btheft\b", r"stolen", r"prowler",
        r"trespass", r"someone (in|inside|outside) the house",
    ],
    "fabrication-mcp-server": [
        r"printer", r"3-? ?d print", r"print job", r"filament", r"nozzle", r"print bed",
        r"octoprint", r"moonraker",
    ],
    "robotics-mcp-server": [
        r"drone", r"robot ?dog", r"\bpatrol", r"telemetry", r"quadcopter",
    ],
    "phase4-mcp-server": [
        r"propose (a )?change", r"review (the |your )?code", r"upgrade yourself",
    ],
    "utility-mcp-server": [
        r"\btime\b", r"\bdate\b", r"what day", r"\btoday\b", r"tomorrow", r"tonight",
        r"o'?clock", r"system status", r"hardware", r"\bgpu\b", r"\bcpu\b", r"\bram\b",
        r"uptime", r"which model", r"what model", r"your capabilit", r"what can you (do|help)",
        r"what do you do", r"your (tools|servers)",
    ],
    "research-mcp-server": [
        r"search", r"look ?up", r"google", r"on the (web|internet)", r"\bnews\b", r"weather",
        r"forecast", r"\bprice\b", r"how much (does|is|are).*cost", r"latest", r"who won",
        r"\bstock\b",
    ],
    "memory-mcp-server": [
        # memory writes (so drafting a memory still happens on the full path)
        r"remember", r"note that", r"don'?t forget", r"make a note", r"keep in mind", r"\bforget\b",
    ],
    # NEW in Phase 23, and a bug fix rather than an addition for scoping's sake. MAIN_SYSTEM_PROMPT
    # devotes a whole section to engineering design requests ("design, sketch, draw, or lay out
    # something technical" -> ui-bridge-mcp-server__update_design_state), and NOT ONE of those words
    # was a trigger. So "draw me a mounting bracket" hit no trigger, fell through to the classifier,
    # and the classifier's own prompt never mentions drawing either - meaning it could be answered
    # on Phase 18's tool-free fast path, where update_design_state does not exist and the model can
    # only describe a drawing it was unable to publish. Found by auditing this vocabulary for
    # completeness while keying it by server.
    "ui-bridge-mcp-server": [
        r"\bdraw\b", r"\bsketch", r"\bdiagram", r"schematic", r"blueprint", r"\bdesign\b",
        r"lay ?out", r"\bwiring\b", r"\bcircuit", r"\bpcb\b", r"enclosure", r"\bbracket\b",
        r"\bmount(ing)?\b", r"\bcad\b", r"\bsvg\b",
    ],
    # Phase 25. Overlaps ui-bridge's design vocabulary on purpose - a design request should put
    # BOTH in scope, since the natural flow is "model it (openscad) and show me (ui-bridge)", and
    # scoping unions every matched group rather than picking one. The terms here are the ones that
    # imply a real 3D part rather than a 2D sketch.
    #
    # This group carries the risk §8.24 flagged: the vocabulary is hand-maintained and a gap is
    # SILENT - a missing word does not fail a test, it quietly removes this server from turns that
    # needed it. That is the standing cost of every server added from here on.
    "openscad-mcp-server": [
        r"\bopenscad\b", r"\bscad\b", r"\bstl\b", r"3-? ?d model", r"\bmodel (me|a|an)\b",
        r"\bparametric\b", r"\bextrude", r"\bfillet\b", r"\bchamfer\b", r"\bmesh\b",
        r"\bprintable\b", r"print(?:able)? (?:a |an )?(?:part|bracket|holder|mount|case)",
        r"\bbracket\b", r"enclosure", r"\bcad\b", r"\bmillimet(?:er|re)", r"\bmm\b",
    ],
}

_TOOL_TRIGGER_WORDS = [word for words in SERVER_TRIGGER_WORDS.values() for word in words]
_TOOL_TRIGGER_RE = re.compile("|".join(_TOOL_TRIGGER_WORDS), re.IGNORECASE)
_SERVER_TRIGGER_RES: dict[str, re.Pattern[str]] = {
    server: re.compile("|".join(words), re.IGNORECASE)
    for server, words in SERVER_TRIGGER_WORDS.items()
}

# Servers included on EVERY scoped turn, matched or not, because MAIN_SYSTEM_PROMPT instructs the
# model to reach for them unprompted - so a keyword match is the wrong test for whether they are
# needed:
#   - memory: "When you learn something durable and worth recalling later ... call draft_memory."
#     That can happen on a turn about anything, and the user never says "remember" for it. Dropping
#     memory from a scoped turn would silently re-break Phase 8.B's learning loop.
#   - ui-bridge: the design and face-recognition sections both tell the model to publish state
#     after doing something else, so the need is a consequence of another tool's result rather than
#     of the user's wording.
# Both are small, so carrying them always costs little.
ALWAYS_AVAILABLE_SERVERS: frozenset[str] = frozenset(
    {"memory-mcp-server", "ui-bridge-mcp-server"}
)

# Obviously tool-free openers - answered instantly with no model call at all. Kept tiny and
# unambiguous; anything not caught here (and not a trigger) falls through to the classifier.
_SMALLTALK_RE = re.compile(
    r"^\s*(hi|hello|hey|yo|good (morning|afternoon|evening|night)|thanks|thank you|thx"
    r"|how are you|how'?s it going|what'?s up|tell me a joke|bye|goodbye)\b",
    re.IGNORECASE,
)
# Pure arithmetic, optionally wrapped in a "what is / calculate / how much is" lead-in. If what's
# left after stripping that lead-in and a trailing '?' is only digits and math operators, it needs
# no tool - Tau does the arithmetic itself.
_MATH_LEAD_RE = re.compile(
    r"^\s*(what'?s|what is|calculate|compute|solve|how much is|how many is)?\s*", re.IGNORECASE
)
_MATH_BODY_RE = re.compile(r"^[\d\s+\-*/×÷%.,()=^x]+$", re.IGNORECASE)


def _is_pure_math(text: str) -> bool:
    body = _MATH_LEAD_RE.sub("", text).strip().rstrip("?").strip()
    # Require at least one digit so a lone "=" or "()" doesn't count as math.
    return bool(body) and bool(re.search(r"\d", body)) and bool(_MATH_BODY_RE.match(body))


def heuristic_tool_need(user_text: str) -> Verdict:
    """Instant, deterministic first pass. "tools" = a real trigger word is present (full path);
    "no_tools" = an obviously tool-free case (arithmetic, greeting); "uncertain" = hand it to the
    classifier. Trigger detection wins over the tool-free openers, so "good morning, what's the
    temperature?" is correctly treated as a tool turn."""
    text = user_text or ""
    if _TOOL_TRIGGER_RE.search(text):
        return "tools"
    if _is_pure_math(text) or _SMALLTALK_RE.search(text):
        return "no_tools"
    return "uncertain"


# Above this share of the registered roster, scoping is not worth doing: the token saving is
# small, and every server dropped is a chance to drop the right one. Falls back to "everything".
_MAX_SCOPED_SHARE = 0.5

# Patterns too generic to JUSTIFY narrowing the roster, though they stay full triggers for the
# tool-need gate (they really do imply a tool is needed) and still join a scope that something
# else already opened.
#
# These are sentence adverbs, not domain nouns: "today", "tomorrow" and "tonight" attach equally
# to a printer, a drone, a VM or a break-in. Letting one of them enable scoping on its own means a
# question about ANY domain can be confidently narrowed to utility-mcp-server - which is exactly
# what happened to "Has there been a break-in today?" before this existed: sole match "today",
# scope {utility, memory, ui-bridge}, security dropped, turn unanswerable.
#
# Note what is deliberately NOT here: `\btime\b` and `\bdate\b`. Those are strong signals for
# get_time/get_date ("what time is it") rather than incidental modifiers, so they still scope.
_WEAK_SCOPE_TRIGGERS = re.compile(r"\btoday\b|tomorrow|tonight|\blatest\b", re.IGNORECASE)


def _strongly_matched(text: str) -> set[str]:
    """Servers matched by at least one pattern that is NOT merely a generic time adverb.

    Re-tests each matched server's patterns individually, dropping any whose only hit is a weak
    one. Cheap: it only runs over the handful of groups that already matched.
    """
    strong: set[str] = set()
    for server, words in SERVER_TRIGGER_WORDS.items():
        for word in words:
            match = re.search(word, text, re.IGNORECASE)
            if match and not _WEAK_SCOPE_TRIGGERS.fullmatch(match.group(0).strip()):
                strong.add(server)
                break
    return strong


def heuristic_servers_needed(
    user_text: str, registered: list[str] | None = None
) -> set[str] | None:
    """Which MCP servers this turn plausibly needs, or None for "cannot tell - use all of them".

    Phase 23. The 2026-07-22 eval showed the model failing to find tools whose descriptions
    matched the request almost verbatim, while replying that no such function existed - which is
    what running out of usable attention over 73 tool schemas (~9,100 prompt tokens) looks like.
    This narrows the roster to the domains a turn is actually about, so the model chooses among
    ~15 tools instead of ~73.

    **THE SAFETY CONTRACT, and it is the inverse of the tool-need gate's.** There, a false "needs
    tools" merely cost latency. Here, a false EXCLUSION is the dangerous direction: a server that
    is not in the toolset cannot be called at all, so the model either says it cannot do something
    it can, or invents the answer. A false INCLUSION costs only tokens. Every rule below therefore
    fails toward including more:

    - No group matched -> None (all servers). An unrecognised request is exactly when guessing is
      least safe, and it is also the case where a wrong guess is least recoverable.
    - Only *generic* words matched -> None. A sentence adverb like "today" is not evidence about
      domain; see _WEAK_SCOPE_TRIGGERS for the concrete turn this rule was written for.
    - More than half the roster matched -> None. Little left to save, and more to lose.
    - ALWAYS_AVAILABLE_SERVERS are added regardless of the wording (see that constant).
    - The result is intersected with `registered`, so a scope naming a server this deployment does
      not run degrades to the servers it does - never to a ValueError out of build_toolset.

    `registered` is the deployment's actual server list; None skips the intersection (tests).
    """
    text = user_text or ""
    matched = {
        server for server, pattern in _SERVER_TRIGGER_RES.items() if pattern.search(text)
    }
    if not matched:
        return None
    # Something domain-specific has to have matched before narrowing is justified. Weakly-matched
    # servers still ride along in `scope` below - they are cheap and might genuinely be wanted -
    # they just cannot be the reason the roster shrank.
    if not _strongly_matched(text):
        return None

    scope = matched | ALWAYS_AVAILABLE_SERVERS
    if registered is not None:
        available = set(registered)
        scope &= available
        # Compare against what this deployment actually runs, not against the vocabulary's idea of
        # the world: on a 3-server install, "half the roster" is 2, and scoping to 2 of 3 saves
        # nothing worth the risk.
        if not scope or len(scope) > max(1, int(len(available) * _MAX_SCOPED_SHARE)):
            return None
        return scope

    if len(scope) > max(1, int(len(SERVER_TRIGGER_WORDS) * _MAX_SCOPED_SHARE)):
        return None
    return scope


_CLASSIFIER_PROMPT = """You are a fast pre-classifier for a home AI called Tau, not its main
assistant. Decide whether answering the user's message REQUIRES calling a tool that reads or
changes real state: home devices (lights, locks, thermostat, cameras, sensors), security, servers
or the Proxmox cluster, 3D printers, the drone, the user's stored personal memories, the current
time or date, Tau's own hardware/model, or a live web search for current or changeable facts.

Answer with exactly ONE word:
- DIRECT - if the message can be answered from general knowledge, reasoning, arithmetic, or the
  conversation alone (definitions, explanations, opinions, general how-to, chit-chat).
- TOOLS - if it needs any of the tools above.

When you are unsure, answer TOOLS. Output only the single word, nothing else."""


class ToolNeedClassifier:
    """The ambiguous-case half of the hybrid gate: one cheap router-model call that decides whether
    a turn with no obvious trigger still needs tools. Conservative by construction - only an
    explicit "DIRECT" diverts to the tool-free fast path; every other outcome (a "TOOLS" reply,
    unparseable text, an exception, a timeout) returns True, keeping the safe full path.
    """

    def __init__(self, router_model: Model):
        self._agent = Agent(router_model, system_prompt=_CLASSIFIER_PROMPT)

    async def needs_tools(self, user_text: str) -> bool:
        try:
            result = await self._agent.run(user_text)
        except Exception:  # noqa: BLE001 - a broken classifier must never take the turn down; fail safe (tools on)
            logger.warning("Tool-need classifier failed; defaulting to the full toolset path", exc_info=True)
            return True
        tokens = re.findall(r"[a-z]+", (result.output or "").lower())
        # Fast path ONLY on a clear, leading "direct"; anything else keeps tools available.
        return not (tokens and tokens[0] == "direct")
