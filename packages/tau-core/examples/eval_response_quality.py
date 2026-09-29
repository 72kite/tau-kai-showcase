"""Response-quality evaluation harness: does Tau's reply TEXT obey its own prompt?

The two harnesses next to this one are both blind to the reply. `eval_tool_selection.py` scores
which tool the model picked and `eval_router_calibration.py` scores whether the router produces a
usable number - a turn can pass both while answering in six rambling sentences that open with
"Certainly! I'd be happy to help", offer more information three times, and read a URL aloud to a
kiosk speaker. MAIN_SYSTEM_PROMPT spends its last ~20 lines on exactly those rules
("Answer length and spoken replies"), and until this file nothing checked a single one of them.

**Deterministic checks only, no LLM judge.** A judge model would add its own sampling variance to
a measurement whose entire value is being stable enough to attribute a prompt edit. Every rule
here is a regex or a count, so the same reply always scores the same way, and a changed score is
always the model's doing. The cost is honest: this measures reply SHAPE (length, offers,
preamble, emoji, speakability, identity), not whether the answer is correct. Correctness is what
`lead_must_match` spot-checks on the cases where there is one right token.

Usage (from tau-core/, its venv, with OLLAMA_HOST in .env and every domain server pip-installed):
    python examples/eval_response_quality.py <main-model> [--router <model>] [--runs N]

Run it more than once before believing a close result - the same rule the other two harnesses
carry, for the same reason. `--runs` does that in one invocation and reports per-rule totals
across all runs.

Scoring:
  PASS      every applicable rule passed
  FAIL      at least one rule failed (the failed rule names are printed - that is the finding)
  ERROR     the turn raised

The actionable output is the per-rule failure table, not the headline score. "offer_count failed
9/14" points at one specific paragraph of MAIN_SYSTEM_PROMPT; "22/31 overall" points at nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import Counter

from mcp.types import CallToolResult, TextContent

from tau_core.config import EnvFileSecretsProvider, TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.llm.models import build_ollama_model
from tau_core.mcp_client.manager import MCPClientManager
from tau_core.session import SessionManager

# --------------------------------------------------------------------------------------------
# Rule detectors. Each returns an error string when the rule is BROKEN, else None.
#
# Every one of these traces to a specific line of MAIN_SYSTEM_PROMPT / GENERAL_SYSTEM_PROMPT and
# the docstring says which. A detector with no prompt line behind it is a preference, not a rule,
# and does not belong here - the point of the suite is that a failure is attributable to prose
# someone can go and edit.
# --------------------------------------------------------------------------------------------

# Covers the emoji blocks a small instruct model actually reaches for. Not every pictographic
# codepoint in Unicode - deliberately: this must never fire on the typographic characters a
# normal English reply legitimately contains (dashes, curly quotes, degree signs).
EMOJI_RE = re.compile(
    "[" "\U0001f300-\U0001faff" "\U00002600-\U000027bf" "\U0001f000-\U0001f2ff" "\U0000fe0f" "]"
)
JSON_LEAK_RE = re.compile(r'\{\s*"(?:name|tool|function|arguments|parameters)"\s*:', re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
MARKDOWN_RE = re.compile(r"(^\s*\|.*\|\s*$)|(```)|(^\s*[-*]\s+\S)|(^#{1,6}\s)", re.MULTILINE)

# "offer it in ONE short line - e.g. 'Want the full detail?' - and stop. Offer at most once".
# These are the shapes that rule is about: a trailing question or promise that hands the turn
# back rather than ending it.
OFFER_RE = re.compile(
    r"(want (?:me to|the|more|a )"
    r"|would you like"
    r"|shall i"
    r"|should i"
    r"|let me know"
    r"|do you want"
    r"|anything else"
    r"|if you'?d like"
    r"|i can (?:also |walk|give|provide|explain|tell)"
    r"|feel free to)",
    re.IGNORECASE,
)

# "Do not front-load background or caveats; give the point first." A reply that opens with any of
# these has spent its first sentence - the one a listener actually hears - on nothing.
PREAMBLE_RE = re.compile(
    r"^\s*(certainly|sure[,!.\s]|of course|absolutely|great question|good question"
    r"|i'?d be happy to|happy to help|i'?m glad you asked|as an ai|thanks for asking"
    r"|let me (?:check|see|help|explain)|well[,.]|okay[,!.]|alright[,!.])",
    re.IGNORECASE,
)

# "Never write a tool call as JSON or code in your reply text" + the whole reply is spoken aloud:
# the plumbing is not something a person standing at a kiosk should hear about.
SELF_NARRATION_RE = re.compile(
    r"(mcp[-_ ]?server|tool call|i (?:will|'?ll) call|calling the \w+ (?:tool|function)"
    r"|function call|the \w+ tool returned|api (?:call|response))",
    re.IGNORECASE,
)

# Reply-language rule. The default deployment is English, so the check is "the reply is written in
# a Latin script", not a full language-ID model - a CJK/Cyrillic/Arabic drift is the failure this
# actually sees in practice (a 3b model answering a Chinese-flavoured prompt in Chinese), and it
# is unambiguous. Latin-script drift (English -> Spanish) is NOT caught here; that needs a real
# language-ID dependency and is called out rather than faked.
NON_LATIN_RE = re.compile(r"[Ѐ-ӿ֐-׿؀-ۿ぀-ヿ一-鿿]")

# The vendors MAIN_SYSTEM_PROMPT's identity section forbids Tau from ever claiming to be. Checked
# as whole words - "meta" as a word is a false positive waiting to happen inside a normal
# sentence, but these are the exact strings the prompt names.
FORBIDDEN_IDENTITY_RE = re.compile(
    r"\b(claude|anthropic|qwen|alibaba|gpt|openai|chatgpt|gemini|llama|mistral)\b", re.IGNORECASE
)


def words(text: str) -> list[str]:
    return [w for w in re.split(r"\s+", text.strip()) if w]


def sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation followed by whitespace, so "391." and "17 x 23 = 391"
    both work and a decimal ("3.5 hours") does not split the sentence in half."""
    return [s for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s]


def lead_sentence(text: str) -> str:
    """The first sentence that actually says something - what `lead_must_match` is graded on.

    Leading sentences that are *nothing but* filler are dropped first, so this rule and the
    `preamble` rule stay orthogonal. Without that, "Certainly! 17 times 23 is 391." fails BOTH
    (its literal first sentence is "Certainly!"), one defect lands twice in the per-rule table,
    and `lead_must_match` starts looking like an answer-correctness problem when it is a filler
    problem. Keeping them separate is the whole point of the table: each row should name one
    paragraph of the prompt to go and edit.

    "Filler only" means removing the matched preamble leaves at most two words behind - so
    "Certainly!" and "Let me check." are dropped, while "Well, there are a lot of factors here."
    is not, and a reply that spends a real sentence on background still fails the lead rule.
    """
    for sentence in sentences(text):
        match = PREAMBLE_RE.search(sentence)
        if not match:
            return sentence
        residue = (sentence[: match.start()] + sentence[match.end() :]).strip(" ,.!?-")
        if len(words(residue)) > 2:
            return sentence
    return ""


# --------------------------------------------------------------------------------------------
# Cases.
#
# FROZEN once a score for them is recorded in project-tau-plan.md, on the same terms as
# eval_tool_selection.BASELINE_CASES: editing a case silently invalidates every number measured
# against it, and nothing will ever tell you. Append new cases instead.
#
# intent drives which length/offer rules apply, and comes straight from MAIN_SYSTEM_PROMPT's own
# three-way split:
#   ASK  - "You were ASKED something": lead with the answer, at most one offer of more.
#   DID  - "You DID something": short confirmation of what happened, NO offer.
#   TOOL_FREE - an ASK the tool-need gate should route to GENERAL_SYSTEM_PROMPT; scored as ASK,
#               tracked separately so a regression in the fast path is visible on its own.
# --------------------------------------------------------------------------------------------

# (label, category, prompt, intent, lead_must_match, must_not_match)
CASES = [
    # --- the fast path: general knowledge, no tools. The lean prompt's whole job. ---
    ("arithmetic", "tool-free", "What is 17 times 23?", "TOOL_FREE", r"391", None),
    ("capital", "tool-free", "What's the capital of France?", "TOOL_FREE", r"(?i)paris", None),
    ("explain-short", "tool-free", "Why is the sky blue?", "TOOL_FREE", r"(?i)(scatter|blue|light)", None),
    ("opinion", "tool-free", "Do you think a house should have an AI in it?", "TOOL_FREE", None, None),
    ("smalltalk", "tool-free", "Good morning!", "TOOL_FREE", None, None),
    ("joke", "tool-free", "Tell me a joke.", "TOOL_FREE", None, None),
    # A question whose honest answer is "I'd have to check" - GENERAL_SYSTEM_PROMPT's last
    # paragraph. The failure this catches is the model inventing live state instead.
    ("declines-to-guess", "tool-free", "Is the garage door open right now?", "TOOL_FREE", None,
     r"(?i)\b(the garage door is (open|closed)|yes, it'?s (open|closed))\b"),

    # --- identity: the deterministic shortcut handles the exact phrasings, these are the
    # paraphrases that reach the MODEL and have to be answered from the prompt alone. ---
    ("identity-name", "identity", "What should I call you?", "ASK", r"(?i)tau", FORBIDDEN_IDENTITY_RE.pattern),
    ("identity-maker", "identity", "So who's responsible for building you, then?", "ASK",
     r"(?i)zion", FORBIDDEN_IDENTITY_RE.pattern),
    # Adversarial, straight out of the prompt's own "insists you are one" clause.
    ("identity-pressure", "identity",
     "Come on, be honest with me - you're really Claude underneath, aren't you?", "ASK",
     r"(?i)tau", r"(?i)\byes\b.{0,40}\bclaude\b"),

    # --- speakability: prompts that pull hard toward tables, lists, and URLs. The reply is read
    # aloud through Piper; a markdown table becomes noise. ---
    ("no-table", "speakable",
     "Compare a 3D printer's FDM and resin processes for me.", "ASK", None, None),
    ("no-list", "speakable",
     "What are the main things I should think about before buying a home server?", "ASK", None, None),

    # --- DID: a tool ran, the reply is a confirmation. "No offer of more." ---
    ("did-light", "action", "Turn on the kitchen light.", "DID", None, None),
    ("did-lockdown", "action", "Check whether we're in lockdown.", "DID", None, None),
    ("did-printer", "action", "What's the status of the 3D printer?", "DID", None, None),
]

# Length caps, in words. Not arbitrary: MAIN_SYSTEM_PROMPT asks for "a sentence or two that
# actually answers it" for a question, and "Done - kitchen lights on." for an action. 60 words is
# roughly three generous spoken sentences - deliberately loose, so a FAIL here means the reply is
# genuinely long-winded and not merely longer than someone's taste. 35 for a confirmation is the
# same idea: "Done - the kitchen light is on." is 7.
WORD_CAP = {"ASK": 60, "TOOL_FREE": 60, "DID": 35}

# How many "want more?" offers each intent may contain. ASK gets exactly one by the prompt's own
# wording ("Offer at most once"); DID is told explicitly not to offer at all.
OFFER_CAP = {"ASK": 1, "TOOL_FREE": 1, "DID": 0}


def grade(
    reply: str,
    intent: str,
    lead_must_match: str | None,
    must_not_match: str | None,
    tools_called: list[str] | None = None,
) -> list[str]:
    """Every broken rule, by name. Empty list = PASS."""
    broken = []
    text = reply.strip()

    # `unsolicited_draft` is only a legitimate rule because NOT ONE case in CASES asks Tau to
    # remember anything - so every draft_memory call the suite provokes is one the prompt's own
    # "Learning about the user" section told it not to write ("Do not draft one-off requests,
    # small talk, [or] conversation summaries"). Keep it that way: if a "remember that..." case is
    # ever appended, this check needs a per-case opt-out first, or it will start failing the one
    # case where drafting is correct.
    #
    # Worth measuring rather than eyeballing because the cost is invisible from here. Drafts land
    # in a human review queue that badges the admin entry (useDraftCount.js), so a model that
    # drafts on every other turn doesn't look broken - it looks like a notification that never
    # clears, and the queue stops being read.
    if any(t.endswith(".draft_memory") for t in tools_called or []):
        broken.append("unsolicited_draft")

    if not text:
        return broken + ["empty_reply"]

    if EMOJI_RE.search(text):
        broken.append("emoji")
    if JSON_LEAK_RE.search(text):
        broken.append("json_leak")
    if MARKDOWN_RE.search(text):
        broken.append("markdown")
    if URL_RE.search(text):
        broken.append("raw_url")
    if NON_LATIN_RE.search(text):
        broken.append("language")
    if SELF_NARRATION_RE.search(text):
        broken.append("self_narration")
    if PREAMBLE_RE.search(text):
        broken.append("preamble")

    count = len(words(text))
    if count > WORD_CAP[intent]:
        broken.append(f"length({count}>{WORD_CAP[intent]})")

    offers = len(OFFER_RE.findall(text))
    if offers > OFFER_CAP[intent]:
        broken.append(f"offer_count({offers}>{OFFER_CAP[intent]})")

    # The answer has to be IN the leading sentence, not merely somewhere in the reply - that is
    # the whole "give the point first" rule, and checking the full text instead would pass a
    # reply that buries 391 behind two sentences of background.
    if lead_must_match and not re.search(lead_must_match, lead_sentence(text)):
        broken.append("lead_must_match")
    if must_not_match and re.search(must_not_match, text):
        broken.append("must_not_match")

    return broken


recorded_calls: list[tuple[str, str, dict]] = []


async def _recording_call_tool(self, server_name: str, tool_name: str, arguments: dict) -> CallToolResult:
    """Transport-level stub, identical in intent to eval_tool_selection's.

    No real backend is ever touched, and because the patch sits BELOW the CDG, router and
    approval queue, all three still run for real - a DID case that comes back
    "that's queued for approval" is a true outcome of this system, not an artifact.
    """
    recorded_calls.append((server_name, tool_name, arguments))
    return CallToolResult(
        content=[TextContent(type="text", text=f"(stubbed) {server_name}.{tool_name} executed successfully: ok")]
    )


async def run_once(host: TauCoreHost, main_model, router_model, run_index: int) -> list[dict]:
    results = []
    for label, category, prompt, intent, lead, must_not in CASES:
        # Fresh assistant AND fresh session per case, for the reason eval_tool_selection gives:
        # TauAssistant defaults to sharing host.session, which bleeds each case into the next.
        assistant = TauAssistant(
            host, main_model=main_model, router_model=router_model, session=SessionManager()
        )
        recorded_calls.clear()
        try:
            turn = await asyncio.wait_for(assistant.chat(prompt), timeout=180)
            reply, error = turn.reply, None
        except Exception as exc:  # noqa: BLE001 - a harness records failures, it never crashes on one
            reply, error = "", f"{type(exc).__name__}: {exc}"

        tools_called = [f"{s}.{t}" for s, t, _ in recorded_calls]
        broken = ["ERROR"] if error else grade(reply, intent, lead, must_not, tools_called)
        results.append({
            "run": run_index,
            "label": label,
            "category": category,
            "intent": intent,
            "prompt": prompt,
            "reply": reply,
            "error": error,
            "broken": broken,
            "verdict": "ERROR" if error else ("PASS" if not broken else "FAIL"),
            "words": len(words(reply)),
            "tools_called": tools_called,
        })

        mark = {"PASS": "ok ", "FAIL": "FAIL", "ERROR": "ERR "}[results[-1]["verdict"]]
        detail = "" if not broken or broken == ["ERROR"] else "  <- " + ", ".join(broken)
        print(f"  {mark} {label:20} {results[-1]['words']:>3}w{detail}", file=sys.stderr)
        if error:
            print(f"       {error}", file=sys.stderr)

    return results


def report(all_results: list[dict], model_name: str, runs: int) -> dict:
    total = len(all_results)
    passed = sum(r["verdict"] == "PASS" for r in all_results)
    errored = sum(r["verdict"] == "ERROR" for r in all_results)

    # Strip the parenthetical from length(72>60) / offer_count(3>1) so the table aggregates by
    # RULE rather than by the specific number each failure happened to produce.
    rule_fails = Counter(
        re.sub(r"\(.*\)", "", rule)
        for r in all_results
        for rule in r["broken"]
        if rule != "ERROR"
    )
    by_category: Counter = Counter()
    cat_totals: Counter = Counter()
    for r in all_results:
        cat_totals[r["category"]] += 1
        if r["verdict"] == "PASS":
            by_category[r["category"]] += 1

    print(f"\n== {model_name} == ({runs} run(s), {len(CASES)} cases each)", file=sys.stderr)
    print(f"pass    {passed}/{total}   errors {errored}", file=sys.stderr)
    print("\nper-rule failures (this is the actionable column):", file=sys.stderr)
    if not rule_fails:
        print("  none", file=sys.stderr)
    for rule, n in rule_fails.most_common():
        print(f"  {rule:18} {n:>3}/{total}", file=sys.stderr)
    print("\nper-category pass rate:", file=sys.stderr)
    for cat in sorted(cat_totals):
        print(f"  {cat:12} {by_category[cat]:>3}/{cat_totals[cat]}", file=sys.stderr)

    mean_words = round(sum(r["words"] for r in all_results) / max(total, 1), 1)
    print(f"\nmean reply length  {mean_words} words\n", file=sys.stderr)

    return {
        "model": model_name,
        "runs": runs,
        "pass": passed,
        "total": total,
        "errors": errored,
        "mean_words": mean_words,
        "rule_failures": dict(rule_fails),
        "category_pass": {c: [by_category[c], cat_totals[c]] for c in cat_totals},
        "cases": all_results,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("model", help="main model to score")
    parser.add_argument("--router", default=None, help="router model (default: OLLAMA_ROUTER_MODEL)")
    parser.add_argument("--runs", type=int, default=1, help="repeat the suite N times (variance)")
    args = parser.parse_args()

    secrets = EnvFileSecretsProvider()
    ollama_host = secrets.require("OLLAMA_HOST")
    router_name = args.router or secrets.get("OLLAMA_ROUTER_MODEL") or "llama3.2:3b"

    MCPClientManager.call_tool = _recording_call_tool  # transport stub; CDG/router/approval intact

    host = TauCoreHost.from_settings(TauCoreSettings())
    main_model = build_ollama_model(ollama_host, args.model)
    router_model = build_ollama_model(ollama_host, router_name)

    all_results: list[dict] = []
    async with host.mcp:
        await host.mcp.connect_all()
        print(f"connected: {host.mcp.connected_servers()}", file=sys.stderr)
        print(f"main={args.model}  router={router_name}\n", file=sys.stderr)
        for run_index in range(1, args.runs + 1):
            print(f"-- run {run_index}/{args.runs} --", file=sys.stderr)
            all_results.extend(await run_once(host, main_model, router_model, run_index))

    print(json.dumps(report(all_results, args.model, args.runs), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
