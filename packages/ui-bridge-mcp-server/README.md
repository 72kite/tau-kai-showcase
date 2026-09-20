# ui-bridge-mcp-server

The UI bridge MCP server for Project Tau — see [`project-tau-plan.md`](../project-tau-plan.md)
Section 6. Phase 3's aggregation layer: collects state from domain servers (voice, home
assistant, vision, security) and exposes it as MCP Resources so the frontend subscribes
directly without a separate REST API layer.

## Layout

```
src/ui_bridge_mcp_server/
  ui_state.py         UIState, TranscriptionState, SecurityState, DeviceState, VisionState
  server.py            FastMCP server: Resources and update tools
tests/
  test_ui_state.py    UIState dataclass tests
  test_server.py      tool functions and Resource handling
```

## Resources

The frontend subscribes to these MCP Resources to receive real-time updates:

- `ui://state` — full aggregated state (transcription, security, devices, vision, recognition)
- `ui://transcription` — current transcription text and active state
- `ui://security` — lockdown status and incident log count
- `ui://devices` — device counts and online status from Home Assistant
- `ui://vision` — camera active state, snapshot (base64), scene description
- `ui://recognition` — most recent face-recognition event: person_id, role, e-ink portrait
  (svg/ascii_art), recognized_at

## Tools

Update tools allow tau-core or domain servers to push state changes:

- `update_transcription(text, is_active)` — Tau's transcribed input or response
- `update_security_state(lockdown_active, reason, intrusion_count)` — from security-mcp-server
- `update_devices_state(device_count, online_count)` — from home-assistant-mcp-server
- `update_vision_state(camera_active, snapshot_b64, description)` — from vision-mcp-server
- `update_recognition_state(person_id, role, svg="", ascii_art="")` — publishes a "USER
  RECOGNIZED" e-ink portrait card; called by Tau after `memory-mcp-server__match_face` +
  `get_person_profile` identify someone (see `tau_core.llm.agent.MAIN_SYSTEM_PROMPT` for the
  portrait-caching contract - reuse a profile's cached `portrait_svg`/`portrait_ascii` if
  present, only draw and cache a new one via `memory-mcp-server__set_person_portrait` if not).
  The frontend auto-dismisses this a few seconds after `recognized_at` on its own.
- `clear_recognition_state()` — hides the recognition card early; not needed for the normal
  auto-dismiss case.

## Design note

In a full implementation, tau-core would connect to each domain server's stdio interface and
stream Resource updates as they occur. For now, this is a simple in-memory UIState that can
be updated via tool calls. The frontend consumes Resources via WebSocket subscription (MCP
supports this natively) and re-renders on each update.

**Durable transcript (Phase 14, §10.2 #6).** The unified cross-device log and per-device buckets
are persisted to `TAU_TRANSCRIPT_STORE_PATH` (a volume in compose; default on-disk, atomic writes,
reloaded on boot) so they survive a container restart — 6.D treats this as the durable record and
6.F removed kiosk scrollback on that basis. Encrypted at rest when `TAU_MASTER_KEY` is set (it holds
conversation content). Best-effort: a persist failure never downs a chat turn, and a corrupt store
starts empty rather than crashing the bridge. Set `TAU_TRANSCRIPT_STORE_PATH=""` for pure in-memory
(what the tests use). Bounded on **two** dimensions so persistence can't grow without limit: each
bucket is a ring buffer (`MAX_TRANSCRIPT_HISTORY`), and the *number* of per-device buckets is
LRU-capped (`TAU_TRANSCRIPT_MAX_DEVICES`, default 64) since `device_id` is a client-supplied opaque
string — past the cap the least-recently-updated device's bucket is dropped (Phase 15 #2), mirroring
tau-core's `TAU_SESSION_MAX_DEVICES`. Durable retention, not an unbounded archive. The rest of
UIState is still in-memory.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `ui-bridge-mcp-server`, spawned via
`python -m ui_bridge_mcp_server.server`. Install with
`pip install -e ../ui-bridge-mcp-server` into tau-core's `.venv`.

This server has **no external dependencies** — it aggregates state from other MCP servers,
all of which are assumed to be running and reachable from tau-core.

## Frontend consumption

The frontend (web app in Phase 3.2) connects to tau-core's MCP client and subscribes to
these Resources. Example (pseudocode):

```javascript
const client = new MCPClient('ws://localhost:3000/mcp');
const stateResource = await client.subscribeResource('ui://state');
stateResource.on('update', (data) => {
  // Update 3D atom, voice overlay, panels, etc.
  updateUI(JSON.parse(data.text));
});
```

## Manual smoke test

Install and run, then connect via `tau_core.mcp_client.MCPClientManager` with a `ServerConfig`
pointing at `python -m ui_bridge_mcp_server.server`. Call `get_ui_state()` (the `ui://state`
resource) to confirm the server is wired correctly and returns JSON.
