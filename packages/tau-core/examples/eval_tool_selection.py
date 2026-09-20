"""Tool-selection evaluation harness for Tau's main model.

Boots the real tau-core host against the real production registry (servers.yaml, all 10 domain
servers over stdio) so the model sees the exact tool roster a live deployment exposes, then runs
a fixed set of unambiguous prompts and records which tools the model actually called.

MCPClientManager.call_tool is monkeypatched to record the call and return a canned success, so:
- no real Proxmox/HA/camera/drone backend is touched, ever;
- the CDG, router-confidence check, and approval queue still run for real (the patch sits at
  the transport layer, below all of them).

Usage (from tau-core/, its venv, with OLLAMA_HOST in .env and every domain server pip-installed):
    python examples/eval_tool_selection.py <main-model> [<router-model>]

Results that drove the 2026-07-11 model recommendation live in project-tau-plan.md Section 10's
"LLM model fit notes" and infra/ollama/models.md.

Scoring per case:
  PASS       expected tool called (or correctly no tool)
  WRONG_TOOL called a tool, but not the expected one
  NO_CALL    expected a tool, model called nothing
  SPURIOUS   expected no tool, model called one anyway
  JSON_LEAK  reply text contains raw tool-call-shaped JSON (counted independently)
"""

from __future__ import annotations

import asyncio
import json
import re
import sys

from mcp.types import CallToolResult, TextContent

from tau_core.config import EnvFileSecretsProvider, TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.llm.agent import TauAssistant
from tau_core.llm.models import build_ollama_model
from tau_core.mcp_client.manager import MCPClientManager
from tau_core.session import SessionManager

# (prompt, expected server, expected tool, alternates) - expected tool None means "no tool call is
# correct". Alternates are also-acceptable tools, e.g. list_devices before call_service is fine.
#
# BASELINE_CASES is FROZEN. These are the exact 11 cases behind every score in
# project-tau-plan.md §10's model-fit notes (7b 5/11, 14b-q3 4/11, 14b-q4 3/11, all 2026-07-22).
# Editing one silently invalidates that history and there is no way to notice afterwards - the
# score still prints, it just no longer means what the plan says it means. Add new cases to
# EXTENDED_CASES below instead; the harness reports both numbers separately.
BASELINE_CASES = [
    ("What is 17 times 23?", None, None, set()),
    ("Is there a security lockdown active right now?", "security-mcp-server", "get_intrusion_status", set()),
    ("List the VMs running on the Proxmox cluster.", "proxmox-mcp-server", "list_vms", set()),
    (
        "Turn on the kitchen light.",
        "home-assistant-mcp-server",
        "call_service",
        {("home-assistant-mcp-server", "get_entity_state"), ("home-assistant-mcp-server", "list_devices")},
    ),
    ("What is the drone's current battery level and position?", "robotics-mcp-server", "get_telemetry", set()),
    ("Take a snapshot from the camera and tell me what you see.", "vision-mcp-server", "get_snapshot",
     {("vision-mcp-server", "describe_scene")}),
    ("What's the status of the 3D printer?", "fabrication-mcp-server", "get_printer_status", set()),
    ("Thanks Tau, that's all for now - have a good night.", None, None, set()),
    # Phase 6.B (utility) and Phase 8 (research + draft memory). Added 2026-07-15: the original
    # eight predate all of them, so a green score said nothing about whether the newest - and
    # most tool-dependent - features actually fire. Phase 8 in particular is ENTIRELY tool-driven:
    # if the model won't call draft_memory, "Tau learns about you" is a feature that does nothing.
    ("What time is it?", "utility-mcp-server", "get_time", {("utility-mcp-server", "get_date")}),
    (
        "Look up what the newest stable Rust release is - search the web.",
        "research-mcp-server",
        "search_web",
        {("research-mcp-server", "fetch_page")},
    ),
    (
        "Remember that I like the kitchen lights at 40% after 10pm.",
        "memory-mcp-server",
        "draft_memory",
        # store_memory is a defensible read of "remember this" too - it's the approval-gated
        # sibling, and picking it is a governance-safe miss, not a wrong domain.
        {("memory-mcp-server", "store_memory")},
    ),
]

# Added 2026-07-28. **Why the set had to grow before any tuning could be believed:** §10's own
# notes concede "within single-run variance", and with 11 cases one flipped case is 9 points. A
# description change that moved 5/11 to 6/11 was indistinguishable from noise, so the harness
# could not actually settle the question it existed to settle.
#
# Built as PARAPHRASE GROUPS rather than more one-off prompts. Three ways of asking for the same
# thing test whether a tool is findable; one phrasing tests whether it happens to match that
# phrasing. The 2026-07-22 failures were exactly this - `get_printer_status` matched the words
# "printer status" almost verbatim and still lost the case, which a single-phrasing suite reports
# as "printer: fail" and a paraphrase group reports as "printer: 0/3, never found by any wording".
#
# `category` groups cases so per-domain accuracy is visible: "home control 1/4" is a finding,
# "17/31 overall" is a number.
EXTENDED_CASES = [
    # --- home control: the headline failure, in four wordings ---
    # Phase 44: home-assistant-mcp-server gained turn_on/turn_off/set_light_state/
    # set_climate_temperature, which resolve a device/area BY NAME through HA's own intent system
    # - no entity_id lookup needed first. These three cases now expect those tools instead of
    # call_service; call_service (still functional, still CDG-allowed) is kept as an alternate in
    # case the model reasonably falls back to the old two-step path. This is the direct test of
    # Phase 44's hypothesis - home-control has never scored above 1/5 on any model/config tested,
    # and the root cause was named twice: call_service's RPC-style name doesn't read as an intent,
    # and it requires an entity_id the model must resolve itself first. These new tools remove
    # both problems at once rather than retrying the wrapper-tool fix that was already tried and
    # retracted (see Phase 36's memory notes - its apparent win did not survive a repeat-run
    # control). Not yet re-measured live as of this edit - see the honest-gaps note in
    # project-tau-plan.md's Phase 44 entry.
    ("Switch off the porch light.", "home-assistant-mcp-server", "turn_off",
     {("home-assistant-mcp-server", "call_service"), ("home-assistant-mcp-server", "list_devices"),
      ("home-assistant-mcp-server", "get_entity_state")}),
    ("Dim the lounge lamps to 30 percent.", "home-assistant-mcp-server", "set_light_state",
     {("home-assistant-mcp-server", "call_service"), ("home-assistant-mcp-server", "list_devices"),
      ("home-assistant-mcp-server", "get_entity_state")}),
    ("Set the bedroom thermostat to 20 degrees.", "home-assistant-mcp-server", "set_climate_temperature",
     {("home-assistant-mcp-server", "call_service"), ("home-assistant-mcp-server", "list_devices"),
      ("home-assistant-mcp-server", "get_entity_state")}),
    ("What temperature is it in the bedroom?", "home-assistant-mcp-server", "get_entity_state",
     {("home-assistant-mcp-server", "list_devices")}),

    # --- security: the tool is named "intrusion", the user says "lockdown" ---
    ("Are we in lockdown?", "security-mcp-server", "get_intrusion_status", set()),
    ("Has there been a break-in today?", "security-mcp-server", "get_intrusion_status", set()),
    ("Is the house secure?", "security-mcp-server", "get_intrusion_status", set()),

    # --- infrastructure: the model invented `system-mcp-server__list_vms` for this ---
    ("What containers are running on the cluster?", "proxmox-mcp-server", "list_vms", set()),
    ("Show me every virtual machine.", "proxmox-mcp-server", "list_vms", set()),

    # --- the "status" collision: each of these must reach its OWN domain, not get_system_status ---
    ("Is the print finished yet?", "fabrication-mcp-server", "get_printer_status", set()),
    ("How hot is the printer bed?", "fabrication-mcp-server", "get_printer_status", set()),
    ("What's the drone's status?", "robotics-mcp-server", "get_telemetry", set()),
    ("How much battery does the drone have left?", "robotics-mcp-server", "get_telemetry", set()),
    # The one case where get_system_status IS correct - the control for the two above. If
    # narrowing its description overshoots, this is what catches it.
    ("What model are you running on?", "utility-mcp-server", "get_system_status", set()),

    # --- vision ---
    ("What does the camera see right now?", "vision-mcp-server", "get_snapshot",
     {("vision-mcp-server", "describe_scene")}),
    ("Check the front door camera.", "vision-mcp-server", "get_snapshot",
     {("vision-mcp-server", "describe_scene")}),

    # --- memory: "remember" competing with a device noun, the 2026-07-22 failure shape ---
    ("Don't forget that I'm allergic to peanuts.", "memory-mcp-server", "draft_memory",
     {("memory-mcp-server", "store_memory")}),
    ("Remember my sister's name is Ada.", "memory-mcp-server", "draft_memory",
     {("memory-mcp-server", "store_memory")}),
    # Deliberately adversarial: a memory request whose object is a device, which is what pulled
    # the model toward "a function to configure or schedule lighting settings" last time.
    ("Remember that I always turn the porch light off before bed.", "memory-mcp-server",
     "draft_memory", {("memory-mcp-server", "store_memory")}),

    # --- research ---
    ("What's the newest version of Python? Look it up.", "research-mcp-server", "search_web",
     {("research-mcp-server", "fetch_page")}),

    # --- utility ---
    ("What's today's date?", "utility-mcp-server", "get_date", {("utility-mcp-server", "get_time")}),
    ("What can you do?", "utility-mcp-server", "describe_capabilities", set()),

    # --- no tool: the SPURIOUS guard. A roster of 73 tools makes over-calling as real a failure
    # as under-calling, and the baseline set had only two of these to catch it.
    ("What's the capital of France?", None, None, set()),
    ("Tell me a joke.", None, None, set()),
    ("Why is the sky blue?", None, None, set()),
    ("Good morning!", None, None, set()),
]

# What each case is testing, for the per-category breakdown. Index-aligned with
# BASELINE_CASES + EXTENDED_CASES.
CATEGORIES = (
    ["no-tool", "security", "infrastructure", "home-control", "robotics", "vision",
     "fabrication", "no-tool", "utility", "research", "memory"]
    + ["home-control"] * 4
    + ["security"] * 3
    + ["infrastructure"] * 2
    + ["fabrication"] * 2 + ["robotics"] * 2 + ["utility"]
    + ["vision"] * 2
    + ["memory"] * 3
    + ["research"]
    + ["utility"] * 2
    + ["no-tool"] * 4
)

CASES = BASELINE_CASES + EXTENDED_CASES
assert len(CATEGORIES) == len(CASES), (
    f"CATEGORIES ({len(CATEGORIES)}) must stay index-aligned with CASES ({len(CASES)})"
)

JSON_LEAK_RE = re.compile(r'\{\s*"(?:name|tool|function)"\s*:', re.IGNORECASE)

recorded_calls: list[tuple[str, str, dict]] = []
# Every call the MODEL attempted, with what the pipeline did to it. Distinct from recorded_calls,
# which only sees calls that reached the transport.
attempted_calls: list[tuple[str, str, str]] = []

_host_call_tool = TauCoreHost.call_tool


async def _recording_host_call_tool(self, server: str, tool: str, arguments: dict, **kwargs):
    """Records what the model TRIED, and what the pipeline did with it.

    Added 2026-07-15, because without it this harness cannot tell the two failure modes apart -
    and they need opposite fixes:

      - the model picked no tool / the wrong tool          -> a MODEL problem
      - the model picked the right tool and the ROUTER      -> a POLICY problem
        scored it below threshold, degrading it to CLARIFY

    Both used to score NO_CALL, because the stub below sits at the transport layer and a
    router-clarified call never reaches the transport. That blind spot pointed the 2026-07-15
    eval at entirely the wrong culprit: a 1/11 score read as "the 7b cannot tool-call", when the
    replies showed the model naming the right tool and the router rejecting all of them.
    """
    outcome = await _host_call_tool(self, server, tool, arguments, **kwargs)
    attempted_calls.append((server, tool, outcome.status.value))
    return outcome


async def _recording_call_tool(self, server_name: str, tool_name: str, arguments: dict) -> CallToolResult:
    recorded_calls.append((server_name, tool_name, arguments))
    return CallToolResult(
        content=[TextContent(type="text", text=f"(stubbed) {server_name}.{tool_name} executed successfully: ok")]
    )


async def main() -> None:
    model_name = sys.argv[1]
    secrets = EnvFileSecretsProvider()
    ollama_host = secrets.require("OLLAMA_HOST")
    router_name = sys.argv[2] if len(sys.argv) > 2 else (secrets.get("OLLAMA_ROUTER_MODEL") or "llama3.1:8b")

    MCPClientManager.call_tool = _recording_call_tool  # transport-level stub; CDG/router untouched
    TauCoreHost.call_tool = _recording_host_call_tool  # records attempts + their pipeline outcome

    settings = TauCoreSettings()
    host = TauCoreHost.from_settings(settings)
    main_model = build_ollama_model(ollama_host, model_name)
    router_model = build_ollama_model(ollama_host, router_name)

    results = []
    async with host.mcp:
        await host.mcp.connect_all()
        print(f"connected: {host.mcp.connected_servers()}", file=sys.stderr)

        for index, (prompt, exp_server, exp_tool, alternates) in enumerate(CASES):
            # fresh assistant AND fresh session per case: TauAssistant defaults to sharing
            # host.session, which would bleed every earlier case's prompt into later ones
            assistant = TauAssistant(
                host, main_model=main_model, router_model=router_model, session=SessionManager()
            )
            recorded_calls.clear()
            attempted_calls.clear()
            try:
                turn = await asyncio.wait_for(assistant.chat(prompt), timeout=180)
                reply = turn.reply
                error = None
            except Exception as exc:  # noqa: BLE001 - eval harness records, never crashes
                import traceback

                reply = ""
                error = f"{type(exc).__name__}: {exc}"
                tb = traceback.format_exc()
                origin = "router" if "router.py" in tb or "ConfidenceScore" in tb else "main-agent"
                error += f" [origin guess: {origin}]"
                print(tb, file=sys.stderr)

            calls = [(s, t) for s, t, _ in recorded_calls]
            substantive = [c for c in calls if c not in alternates]
            attempted = [(s, t) for s, t, _ in attempted_calls]
            clarified = [(s, t) for s, t, st in attempted_calls if st == "clarify"]

            if error:
                verdict = "ERROR"
            elif exp_tool is None:
                verdict = "PASS" if not calls else "SPURIOUS"
            elif (exp_server, exp_tool) in calls:
                verdict = "PASS"
            elif calls and set(calls) <= alternates:
                # Only accepted alternates were called. **This scored NO_CALL until 2026-07-28**,
                # which made the harness under-report: alternates were filtered out of
                # `substantive` (so they could not trigger WRONG_TOOL) but were never checked for
                # a pass, so reaching an explicitly-acceptable tool was indistinguishable from
                # calling nothing at all. Found when "What does the camera see right now?" scored
                # NO_CALL having called vision-mcp-server__describe_scene - a tool listed as
                # acceptable for that very case.
                #
                # That alternates are substitutes and not merely tolerated extras is the harness's
                # own stated intent: see the draft_memory case, whose comment says picking
                # store_memory "is a governance-safe miss, not a wrong domain".
                verdict = "PASS_ALTERNATE"
            elif substantive:
                verdict = "WRONG_TOOL"
            elif (exp_server, exp_tool) in clarified:
                # The model got it RIGHT and the router threw it away. Scoring this as NO_CALL
                # blames the wrong component - the fix is the routing policy, not the model.
                verdict = "ROUTER_BLOCKED"
            elif clarified:
                verdict = "ROUTER_BLOCKED_WRONG_TOOL"
            else:
                verdict = "NO_CALL"

            json_leak = bool(JSON_LEAK_RE.search(reply))
            results.append(
                {
                    "prompt": prompt,
                    "expected": f"{exp_server}__{exp_tool}" if exp_tool else "(no tool)",
                    "calls": [f"{s}__{t}" for s, t in calls],
                    "attempted": [f"{s}__{t}:{st}" for s, t, st in attempted_calls],
                    "verdict": verdict,
                    "json_leak": json_leak,
                    "error": error,
                    "reply": reply[:300],
                    "category": CATEGORIES[index],
                    # Which suite this case belongs to, so the frozen 11 stay separable from the
                    # 2026-07-28 additions no matter how the file is later reordered.
                    "suite": "baseline" if index < len(BASELINE_CASES) else "extended",
                }
            )
            print(f"[{verdict}] {prompt!r} -> attempted={[f'{s}__{t}:{st}' for s, t, st in attempted_calls]}"
                  + (" JSON_LEAK" if json_leak else "") + (f" ({error})" if error else ""),
                  file=sys.stderr)

    passes = sum(1 for r in results if r["verdict"] in ("PASS", "PASS_ALTERNATE"))
    leaks = sum(1 for r in results if r["json_leak"])

    # PASS_ALTERNATE counts as a pass everywhere a score is reported: the model reached a tool
    # the case declares acceptable. It stays a distinct *verdict* so the histogram still shows
    # how often the second-choice tool was the one picked.
    PASSING = ("PASS", "PASS_ALTERNATE")

    def _score(subset: list[dict]) -> dict:
        return {"pass": sum(1 for r in subset if r["verdict"] in PASSING), "total": len(subset)}

    baseline_score = _score([r for r in results if r["suite"] == "baseline"])
    by_category: dict[str, dict] = {}
    for category in sorted({r["category"] for r in results}):
        by_category[category] = _score([r for r in results if r["category"] == category])

    # Failure-mode counts. A run that goes from NO_CALL to WRONG_TOOL has genuinely moved - the
    # model started reaching for tools - even when the pass count is unchanged, and a headline
    # score alone hides that entirely. NO_CALL was the whole story on 2026-07-22: every one of
    # the six failures made no structured call at all.
    verdicts: dict[str, int] = {}
    for r in results:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1

    summary = {
        "model": model_name,
        "router": router_name,
        "pass": passes,
        "total": len(results),
        # Directly comparable with the 2026-07-22 numbers in project-tau-plan.md §10; the overall
        # score above is NOT, since the suite grew on 2026-07-28.
        "baseline_11": baseline_score,
        "by_category": by_category,
        "verdicts": verdicts,
        "json_leaks": leaks,
        "cases": results,
    }
    print(
        f"\n== {model_name} (router {router_name}) =="
        f"\noverall      {passes}/{len(results)}"
        f"\nbaseline 11  {baseline_score['pass']}/{baseline_score['total']}"
        f"   <- compare against §10's 2026-07-22 figures"
        f"\nverdicts     {verdicts}"
        f"\nby category  " + ", ".join(f"{k} {v['pass']}/{v['total']}" for k, v in by_category.items())
        + f"\njson leaks   {leaks}\n",
        file=sys.stderr,
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
