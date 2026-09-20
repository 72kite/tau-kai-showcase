# Default Ollama models

Two models cover the roles project-tau-plan.md Section 3 asks for ("a strong tool-calling model + a smaller fast one for routing"). Live-evaluated 2026-07-11 against the full 10-server production tool roster (8 unambiguous prompts, MCP transport stubbed, CDG/router live — see project-tau-plan.md Section 10's "LLM model fit notes" for the full matrix):

| Role | Model | Why |
|---|---|---|
| Tool-calling / main reasoning | `qwen2.5:14b-instruct` | Best evaluated: 7/8 correct tool selection, zero raw-JSON leakage, consistent across runs. (`llama3.1:8b` managed 4/8 with frequent tool-call-JSON-as-text leakage; `hermes3:8b` hit 7/8 once but swung to 4/8 on rerun; `qwen2.5-coder:14b` never made a structured tool call at all — don't use the `-coder` fine-tune here.) |
| Router (`OLLAMA_ROUTER_MODEL`, confidence pre-check) | `qwen2.5:14b-instruct` (same as main, deliberately) | The only tested model whose scores are *calibrated*: 0.7-0.9 on clearly-matching read-only calls, stable across repeats. `llama3.1:8b` swung ±0.2 around the threshold on identical inputs (median-of-3 sampling was added to tau-core because of this, but its mean was ~0.55 - miscalibrated, not just noisy); `qwen2.5:0.5b` scored ~0.0 on everything; `hermes3:8b` flatlined at 0.0 even with lenient text parsing. Sharing one model with `OLLAMA_MODEL` also means Ollama keeps a single model resident instead of swapping two. A purpose-built small function-caller (`functionary-small-v3.2`, `firefunction-v2`) remains worth evaluating if router latency matters. |

The router is defended in depth in `tau_core.llm.router` regardless of model choice: structured output first, then a lenient text parse that salvages a confidence number from envelope-wrapped/messy replies, then median-of-3 sampling so one bad sample can't flip a decision, then a 0.0 fallback (forces a clarifying question, never crashes the turn). The routing threshold default is 0.5 (`TAU_ROUTING_CONFIDENCE_THRESHOLD`), calibrated against the observed score distributions.

Swap either by editing `pull-models.sh` / `pull-models.ps1` and re-running them, then updating `OLLAMA_MODEL` / `OLLAMA_ROUTER_MODEL` in `tau-core/.env` accordingly.
