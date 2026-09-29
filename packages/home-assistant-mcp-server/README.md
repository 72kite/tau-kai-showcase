# home-assistant-mcp-server

The Home Assistant MCP server for Project Tau — see
`project-tau-plan.md` Section 5.2. Phase 2's first domain server: wraps
Home Assistant's REST API as MCP tools. Independently deployable, like every domain server in
Tau's architecture — this package does not depend on `tau-core`.

## Layout

```
src/ha_mcp_server/
  client.py   HomeAssistantClient - thin async httpx wrapper around HA's REST API
  server.py   FastMCP server: tool definitions, .env loading, security-domain gating
tests/
  test_client.py   HomeAssistantClient against httpx.MockTransport - no real network
  test_server.py   tool functions called directly, with a monkeypatched client seam
```

## Tools

- `list_devices()` — every entity's `{entity_id, state, friendly_name}`.
- `get_entity_state(entity_id)` — raw state + attributes for one entity.
- `turn_on(name=None, area=None, domain="light")` / `turn_off(...)` — the preferred tools for
  "turn on/off X". Resolve a device/area **by name**, through Home Assistant's own Assist intent
  system (`POST /api/intent/handle`, `HassTurnOn`/`HassTurnOff`) — no `entity_id` lookup needed
  first. `domain` is restricted to `light`/`switch`/`fan`/`media_player`/`humidifier`/`valve`
  (`INTENT_ALLOWED_DOMAINS`); `lock`/`alarm_control_panel`/`cover` are refused here on purpose,
  since HA's own HassTurnOn/HassTurnOff intents cover `cover` by default and this tool must not
  become a second, ungated way to open a garage door.
- `set_light_state(name=None, area=None, brightness=None, color=None, temperature=None)` — dim/
  brighten/recolor a light (`HassLightSet`) — always light-only by construction, no domain to gate.
- `set_climate_temperature(temperature, name=None, area=None)` — set a thermostat
  (`HassClimateSetTemperature`) — always climate-only, no domain to gate.
- `call_service(domain, service, entity_id, data=None)` — the escape hatch for anything the four
  tools above don't cover (media playback control, fan presets, other domains). Still refuses
  `lock` / `alarm_control_panel` / `cover`, and still needs a real `entity_id` (use
  `list_devices`/`get_entity_state` to find one) since it doesn't go through intent resolution.
- `call_security_service(domain, service, entity_id, data=None)` — only `lock` /
  `alarm_control_panel` / `cover`. This is the tool name `tau-core/config/cdg_rules.yaml`'s
  `home-assistant-security-needs-approval` rule gates behind human approval — the split exists
  because the CDG only matches on `(server, tool)` name, never arguments, so a single generic
  `call_service` tool would have no name-level hook to gate lock/alarm actions specifically.

**Requires HA's `intent:` integration** (add `intent:` to Home Assistant's `configuration.yaml`)
for `turn_on`/`turn_off`/`set_light_state`/`set_climate_temperature` to work — `call_service`/
`call_security_service` don't need it, they call HA's plain services API directly.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # fill in HA_URL + a long-lived access token, never commit .env
pytest
```

Generate `HA_TOKEN` in Home Assistant: your profile (bottom left) > Security > Long-Lived Access
Tokens > Create Token.

## Wiring into tau-core

Already registered in `tau-core/config/servers.yaml` as `home-assistant-mcp-server`, spawned via
`python -m ha_mcp_server.server`. That means this package needs to be installed
(`pip install -e ../home-assistant-mcp-server`) into whatever Python environment `tau-core` runs
`command: python` with — simplest for local dev is tau-core's own `.venv`. `HA_URL`/`HA_TOKEN` are
read from *this* package's own `.env` (via `python-dotenv`), not tau-core's — keeps this server's
credentials self-contained regardless of which process spawns it.

The server intentionally reads `HA_URL`/`HA_TOKEN` lazily, per tool call, rather than at process
startup, so it can be registered and its tools listed even before real HA credentials exist —
only calling a tool without them set returns a clear `RuntimeError`.

## Manual smoke test (once you have a real HA instance)

Either:
- Point `tau-core/examples/chat_repl.py` at it (once this package is installed into tau-core's
  venv) and ask it to "turn on the kitchen light" (should execute) vs. "unlock the front door"
  (should come back pending approval, never executed), or
- Connect directly the same way `tau-core/tests/test_mcp_client_manager.py` does for the `echo`
  example server, using `tau_core.mcp_client.MCPClientManager` with a `ServerConfig` pointing at
  `python -m ha_mcp_server.server`.
