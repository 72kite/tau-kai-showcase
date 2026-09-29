# Tau Core

The MCP Host for Project Tau — see `project-tau-plan.md` for the full
build plan. This package is Phase 1: the orchestrator that holds the LLM session, decides which
MCP tools to call, and enforces Tau's non-negotiable rules through the Core Directive Guard.

## Layout

```
src/tau_core/
  cdg/          Core Directive Guard - deterministic, LLM-unreachable policy layer
  approval/     Pending-action queue for human sign-off (used by CDG require_approval effect)
  mcp_client/   MCP client manager - stdio + Streamable HTTP transports (official `mcp` SDK)
  session/      Rolling conversation state + pluggable long-term memory backend.
                MemoryTreeBackend (the default when the host builds its own session) recalls
                memory-mcp-server Memory Tree context each chat turn via host.call_tool
  routing/      Confidence-threshold policy: act vs. ask a clarifying question
  config/       Settings + pluggable secrets provider (local .env for now, Vault later)
  llm/          LLM runtime wiring (Ollama via PydanticAI) - turns a user message into
                TauCoreHost.call_tool() calls and a reply; never talks to MCP servers directly
  host.py       TauCoreHost - wires all of the above into one call_tool() pipeline
  logging_setup.py  JSON audit log every tool call passes through

config/
  cdg_rules.yaml   The ruleset itself. Edited manually and out-of-band only - never through
                   the governed self-upgrade pipeline (Phase 4).
  servers.yaml     Registry of MCP servers Tau Core connects to - real domain servers only;
                   this is what a live deployment loads.
  servers.test.yaml  Test-only registry of the two example fixture servers below. Point
                     TAU_SERVERS_CONFIG_PATH here to smoke-test without domain servers installed.

examples/
  echo_mcp_server.py       Trivial stdio server used to validate the client plumbing.
  dangerous_mcp_server.py  Exposes a shutdown-class tool, used to prove the CDG actually
                           blocks real tool calls, not just its own unit tests.
  chat_repl.py             Manual smoke test for tau_core.llm against a real running Ollama.
  eval_tool_selection.py   Repeatable tool-selection benchmark against a real Ollama: real host
                           + all production servers, MCP transport stubbed, CDG/router live.
                           Drove the model recommendations in infra/ollama/models.md.
```

## How a tool call flows

```
caller -> TauCoreHost.call_tool()
            -> ToolRoutingPolicy.decide()   confidence too low? -> return CLARIFY, stop here
            -> CoreDirectiveGuard.enforce() deny?   -> raise, log, stop here
                                            approval needed and missing/mismatched?
                                                -> submit to PendingActionQueue, return PENDING_APPROVAL
            -> MCPClientManager.call_tool()  actually invoke the tool over MCP
            -> audit log (JSON line)         every outcome - executed, denied, or pending - is logged
```

The CDG has no dependency on the LLM runtime and takes no free-text instructions; it only ever
consults `config/cdg_rules.yaml`. `TauCoreHost.call_tool` is the only path from the rest of the
system to `self.mcp`, so there is no way to reach a real MCP server without going through the
guard first.

Approval tokens are bound to the exact `(server, tool, arguments)` triple they were granted for
(see `tau_core.hashing.hash_arguments`) - approving one shutdown call does not authorize a
different one - and they are single-use: `PendingActionQueue.redeem` marks a request `USED` at the
moment it authorises a call, and `TauCoreHost.call_tool` redeems it exactly when the CDG rule that
matched actually required an approval.

That second half only became true in the Phase 21 security audit. Before it, this paragraph
described an intent rather than the code: `to_approved_action` validated an approval but never
marked it, so one human sign-off authorised the same call an unlimited number of times until its
1h TTL expired - and the unauthenticated `POST /api/tools/{server}/{tool}` accepts an
`approval_request_id`, while the equally unauthenticated `GET /api/approvals` publishes pending
ids alongside arguments that are only redacted when they are biometric or bulky. One approved
`exit_lockdown` meant unlimited `exit_lockdown`. See `tests/test_security_hardening.py`.

## LLM runtime wiring

`tau_core.llm.TauAssistant` sits in front of `TauCoreHost` and is the piece that decides *what* to
call: it runs a PydanticAI agent (Ollama's `OLLAMA_MODEL`, an OpenAI-compatible endpoint) that sees
one tool per connected MCP server tool, named `{server}__{tool}` to avoid collisions. Every one of
those tools is a thin wrapper around `host.call_tool` - PydanticAI's own MCP toolset classes are
deliberately not used, since they'd open a second, unguarded MCP session that bypasses the CDG
entirely. Before each call executes, a second Ollama model (`OLLAMA_ROUTER_MODEL`, see
`infra/ollama/models.md`) scores how well the proposed call matches the user's message; that score
is the `confidence` `ToolRoutingPolicy` already expected. The main reply model is actually a
priority chain (`TauAssistant.from_settings`, `tau_core.llm.models`): vLLM first if `VLLM_HOST`/
`VLLM_MODEL` are set (opt-in - both required together), Ollama's `OLLAMA_MODEL` as the always-on
fallback, then OpenRouter last if `OPENROUTER_API_KEY`/`OPENROUTER_MODEL` are set, chained via
PydanticAI's `FallbackModel` on `ModelAPIError`. The router model stays Ollama-only regardless -
it's a small model queried every turn, not worth moving off the local host. The router is defended in depth
(live-tuned 2026-07-11): structured output, then a lenient text parse that salvages a confidence
from messy replies, then median-of-3 sampling, then a 0.0 fallback that degrades to a clarifying
question - a broken router can never crash a chat turn. `DENIED`/`NEEDS_CLARIFICATION`/
`PENDING_APPROVAL` outcomes are returned to the model as text (not raised), so Tau relays what
happened in its reply instead of crashing. See `tests/test_llm_agent.py` for the proof that a
model-issued call to a dangerous tool still comes back pending approval, and
`examples/chat_repl.py` for a manual round trip against a real Ollama instance.

The main agent can also delegate: `spawn_subagent(task, servers)` runs a fresh, single-task
sub-agent (same Ollama model by default) whose toolset is built by the same `build_toolset`
wrapper machinery, restricted to the MCP servers the parent names. That makes three properties
structural rather than promised: a sub-agent's every tool call still flows through
`host.call_tool` (CDG/approval/audit apply identically, and approvals it triggers surface on
the parent turn); its toolset never contains `spawn_subagent`, so recursion is impossible by
construction; and a runaway sub-agent hits `SUBAGENT_REQUEST_LIMIT` and reports back instead of
spinning. Failures come back to the main model as `SUBAGENT_NOT_STARTED`/`SUBAGENT_FAILED` text,
never as a crashed turn. See `tests/test_subagent.py`.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # fill in real values; never commit .env
pytest
```

## Exit criteria (met)

- Connects to a real MCP server over stdio, lists its tools, calls one, gets a result.
- Every call - allowed, denied, or pending - is written to the audit log as a JSON line.
- A disallowed action (`shutdown_host`, matched by the `no-self-destruct` rule) is blocked until
  a human approves it through the `PendingActionQueue`; a `*cdg*`-matching call is denied outright
  with no approval path at all.

See `tests/test_host_integration.py` for the end-to-end proof.

## Not yet built (later phases)

- ~~Real domain MCP servers (Proxmox, Home Assistant, vision, security, fabrication, robotics)~~ -
  built in Phase 2+; `config/servers.yaml` registers all of them (and only them - the example
  fixture servers moved to `config/servers.test.yaml`).
- Vault-backed secrets (`SecretsProvider` is already an interface for this; only the local `.env`
  backend exists so far).
- Multi-turn PydanticAI message-history replay: `TauAssistant` currently renders recent
  `SessionManager` history as plain text context rather than PydanticAI's typed message history -
  fine for now, worth revisiting once tool-call replay across turns actually matters.
- UI bridge, self-upgrade governance pipeline, robotics - Phases 3-5.
