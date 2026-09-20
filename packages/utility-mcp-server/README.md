# utility-mcp-server

Phase 6.B. The small primitives every other capability assumes exist, plus factual
self-knowledge. All tools are **read-only**, so nothing here is approval-gated (they fall
through to the CDG's `default_effect: allow`).

## Tools

| Tool | Purpose |
|---|---|
| `get_time` | Current wall-clock time, timezone-aware. Tau has no built-in clock — this is the only way it knows the time. |
| `get_date` | Current date (weekday + human form), timezone-aware. |
| `get_system_status` | Hardware (GPU VRAM, RAM, CPU cores), current + recommended model, process uptime. |
| `describe_capabilities` | Which MCP servers are registered and what each does — read from the deployment's `servers.yaml`. |

## Resource

- `tau://identity` — stable, factual identity (name, purpose, operating rules in plain terms), so
  the model answers "who/what are you" from fact rather than a paraphrase of its system prompt.

## Notes

- **Timezone:** set `TAU_TIMEZONE` (IANA name, e.g. `America/New_York`); defaults to the system
  local zone. `tzdata` is a dependency so this works on Windows too (Windows ships no system
  IANA database; Linux/Docker already have one).
- **Hardware:** `get_system_status` soft-imports `tau_core.hardware` (Phase 6.A) when it shares
  the venv, so the GPU/model view appears once tau-core is installed alongside. Absent that, it
  still reports CPU cores, the configured model, and uptime — never an error.
- **Self-knowledge is design-level:** `describe_capabilities` reports what Tau is *designed* with
  (registered servers). "Connected right now" is the host/bridge's question (see the web bridge's
  `/api/health`) — the only component that holds live MCP sessions.

## Register / run

Registered in `tau-core/config/servers.yaml` as `utility-mcp-server`
(`python -m utility_mcp_server.server`, stdio). `pip install -e .` into the tau-core venv for
local dev.

## Tests

`pytest tests` — fully offline (no network, no hardware).
