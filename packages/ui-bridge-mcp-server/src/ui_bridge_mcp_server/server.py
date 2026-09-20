import json
import os
from dataclasses import asdict
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from ui_bridge_mcp_server import crypto_store
from ui_bridge_mcp_server.ui_state import MAX_TRANSCRIPT_DEVICES, TranscriptionState, UIState

server = FastMCP(
    "ui-bridge-mcp-server",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)

# Global UI state. The transcript (the admin-only unified log + per-device histories) is
# persisted to a volume by default (Phase 14, §10.2 #6): before this it was a module-level global
# that died with the container, yet 6.D treated it as the durable cross-device record and 6.F
# removed kiosk scrollback on that basis. Durable by default (like tau-core's approval queue) for
# the same reason - an install shouldn't silently retain older turns *nowhere*. Set
# TAU_TRANSCRIPT_STORE_PATH="" to force pure in-memory (the tests do, via conftest); compose points
# it at a named volume. Encrypted at rest when TAU_MASTER_KEY is set.
_TRANSCRIPT_STORE_PATH = os.getenv("TAU_TRANSCRIPT_STORE_PATH", "./data/transcript.json") or None


def _transcript_max_devices() -> int:
    """LRU cap on per-device transcript buckets (Phase 15 #2), from TAU_TRANSCRIPT_MAX_DEVICES.
    A bad/empty value falls back to the default rather than crashing the bridge on boot."""
    try:
        value = int(os.getenv("TAU_TRANSCRIPT_MAX_DEVICES", ""))
    except ValueError:
        return MAX_TRANSCRIPT_DEVICES
    return value if value >= 1 else MAX_TRANSCRIPT_DEVICES


_ui_state = UIState(
    transcription=TranscriptionState(
        store_path=_TRANSCRIPT_STORE_PATH, max_devices=_transcript_max_devices()
    )
)

# Approval queue state (fetched from phase4-mcp-server)
_approval_queue = []


def _load_approval_queue():
    """Load pending proposals from phase4 proposal store.

    Reads proposals.json straight off the shared phase4-data volume (not over MCP - see
    docker-compose.yml). phase4-mcp-server may write this file encrypted (Phase 10, when
    TAU_MASTER_KEY is set), so it's decrypted here with the same key/salt rather than parsed as
    plain JSON - a plaintext json.load would raise an uncaught UnicodeDecodeError on encrypted
    bytes and take the whole server down.
    """
    global _approval_queue
    proposal_db = Path(os.getenv("PROPOSAL_DB_PATH", "./data/proposals.json"))
    if proposal_db.exists():
        try:
            key = crypto_store.resolve_key(proposal_db)
            proposals = crypto_store.read_json(proposal_db, key)
            # Filter for proposals needing user input
            _approval_queue = [
                p for p in proposals.get("proposals", [])
                if p.get("status") in ["pending_user_decision", "pending_review"]
            ]
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            _approval_queue = []


@server.resource(uri="ui://state")
def get_ui_state() -> str:
    """Get the current aggregated UI state as a JSON Resource.

    The frontend subscribes to this Resource and updates whenever state changes
    (transcription text, device status, camera snapshot, security status, etc).

    Returns a plain JSON string - FastMCP wraps whatever a @server.resource() handler
    returns into its own TextContent envelope automatically (str in, TextContent out over
    the wire). Constructing a TextContent by hand here would get wrapped a second time,
    double-nesting the payload.
    """
    return json.dumps(_ui_state.to_dict())


@server.resource(uri="ui://transcription")
def get_transcription_resource() -> str:
    """Current transcription state, device-neutral (no conversation history). Per-device history
    is served by the get_device_transcript tool; the unified cross-device log by
    get_unified_transcript. Phase 6.D moved history out of this general resource so it can't leak
    one device's chat to another through the bridge's generic resource passthrough."""
    return json.dumps(_ui_state.transcription.public_dict())


@server.resource(uri="ui://security")
def get_security_resource() -> str:
    """Get current security/lockdown state."""
    return json.dumps(asdict(_ui_state.security))


@server.resource(uri="ui://devices")
def get_devices_resource() -> str:
    """Get current device state from home-assistant-mcp-server."""
    return json.dumps(asdict(_ui_state.devices))


@server.resource(uri="ui://vision")
def get_vision_resource() -> str:
    """Get current camera/vision state."""
    return json.dumps(asdict(_ui_state.vision))


@server.resource(uri="ui://design")
def get_design_resource() -> str:
    """Get the current engineering design/sketch state.

    Populated via update_design_state when the user asks Tau to design or sketch something.
    The frontend renders this in a dedicated panel next to the atom - it does not replace or
    interrupt the atom animation or the transcript.
    """
    return json.dumps(asdict(_ui_state.design))


@server.resource(uri="ui://recognition")
def get_recognition_resource() -> str:
    """Get the most recent face-recognition event (e-ink "USER RECOGNIZED" portrait card).

    Populated via update_recognition_state when Tau identifies a known person. Returns a plain
    JSON string like every other resource here - see get_ui_state's docstring: FastMCP wraps
    handler returns in TextContent itself; hand-building one double-nests the payload (and the
    original hand-built version also referenced TextContent without importing it, a NameError
    at import time that prevented this whole server from booting).
    """
    return json.dumps(asdict(_ui_state.recognition))


@server.resource(uri="ui://approvals")
def get_approvals_resource() -> str:
    """Get pending approval queue from Phase 4 self-upgrade pipeline.

    Proposals in 'pending_user_decision' or 'pending_review' status appear here
    so the frontend can display them as overlay cards for manual authorization.
    """
    _load_approval_queue()
    return json.dumps({"pending_count": len(_approval_queue), "proposals": _approval_queue})


@server.tool()
def update_transcription(
    text: str, is_active: bool = True, speaker: str = "tau", device_id: str = ""
) -> str:
    """Update transcription state (called by tau-core's chat bridge).

    speaker: "user"/"tau"/"memory". device_id: which client this turn belongs to (Phase 6.D).
    Each call appends one entry to that device's own history AND to the admin-only unified log.
    A turn with no device_id lands only in the unified log (unattributed) - so callers that
    can identify the device should always pass it, or the turn won't appear in any device view.
    """
    _ui_state.transcription.active = is_active
    if text:
        _ui_state.transcription.update(text, speaker=speaker, device_id=device_id)
    _ui_state.update_timestamp()
    return json.dumps(
        {"status": "transcription_updated", "text": text, "speaker": speaker, "device_id": device_id}
    )


@server.tool()
def get_device_transcript(device_id: str) -> str:
    """One device's own conversation history (Phase 6.D per-device isolation). Returns only the
    turns tagged with this device_id - never another device's, never the unified log. An unknown
    or empty id yields an empty history (fail toward showing nothing)."""
    entries = _ui_state.transcription.device_history(device_id)
    return json.dumps(
        {
            "active": _ui_state.transcription.active,
            "device_id": device_id,
            "history": [asdict(e) for e in entries],
        }
    )


@server.tool()
def get_unified_transcript() -> str:
    """The full cross-device conversation log (every device's turns). Sensitive - this is the
    admin-only view. The bridge exposes it only through the tier-gated /api/transcript/unified
    endpoint and blocks it from the generic tool passthrough; nothing else should call it."""
    entries = _ui_state.transcription.history
    return json.dumps({"history": [asdict(e) for e in entries]})


@server.tool()
def update_security_state(
    lockdown_active: bool, reason: str = "", intrusion_count: int = 0
) -> str:
    """Update security state (called by security-mcp-server or tau-core)."""
    _ui_state.security.lockdown_active = lockdown_active
    _ui_state.security.lockdown_reason = reason
    _ui_state.security.intrusion_count = intrusion_count
    _ui_state.update_timestamp()
    return json.dumps(
        {
            "status": "security_updated",
            "lockdown_active": lockdown_active,
        }
    )


@server.tool()
def update_devices_state(device_count: int, online_count: int) -> str:
    """Update device state (called by home-assistant-mcp-server or tau-core)."""
    _ui_state.devices.device_count = device_count
    _ui_state.devices.online_count = online_count
    _ui_state.update_timestamp()
    return json.dumps(
        {"status": "devices_updated", "online": online_count, "total": device_count}
    )


@server.tool()
def update_vision_state(
    camera_active: bool, snapshot_b64: str = "", description: str = ""
) -> str:
    """Update vision state (called by vision-mcp-server or tau-core)."""
    _ui_state.vision.camera_active = camera_active
    _ui_state.vision.current_snapshot = snapshot_b64
    _ui_state.vision.scene_description = description
    _ui_state.update_timestamp()
    return json.dumps({"status": "vision_updated", "camera_active": camera_active})


@server.tool()
def update_design_state(title: str, description: str, svg: str = "", ascii_art: str = "") -> str:
    """Publish an engineering design/sketch for the frontend's e-ink drawing panel.

    Call this whenever the user asks Tau to design, sketch, or draw something technical
    (a circuit, a mechanical bracket, a wiring diagram, a PCB layout, etc). At least one of
    svg or ascii_art must be provided; if both are given the frontend prefers svg and falls
    back to ascii_art only if SVG rendering is unavailable.

    svg: a self-contained <svg> fragment (include viewBox, no external refs). Match the
    e-ink aesthetic - black strokes only, no fill/gradient/shadow, 2-3px stroke-width, and
    label every dimension/part with monospaced text elements so the drawing is technically
    readable, not just decorative. Use a consistent unit grid so proportions are accurate
    (e.g. 1 SVG unit = 1mm) and state the scale in the description.
    ascii_art: a monospaced fallback sketch (box-drawing characters are fine) for clients
    that can't render SVG.
    """
    if not svg and not ascii_art:
        raise ValueError("update_design_state requires at least one of svg or ascii_art")
    _ui_state.design.update(title, description, svg=svg, ascii_art=ascii_art)
    _ui_state.update_timestamp()
    return json.dumps({"status": "design_updated", "title": title})


@server.tool()
def clear_design_state() -> str:
    """Clear the current design panel (e.g. user asks Tau to start a new sketch from scratch)."""
    _ui_state.design.active = False
    _ui_state.update_timestamp()
    return json.dumps({"status": "design_cleared"})


@server.tool()
def update_recognition_state(person_id: str, role: str, svg: str = "", ascii_art: str = "") -> str:
    """Publish a face-recognition event for the frontend's "USER RECOGNIZED" e-ink portrait card -
    ONLY after Tau has identified a known PERSON (typically memory-mcp-server__match_face
    followed by memory-mcp-server__get_person_profile - role is that profile's access_level).

    NOT for turning anything on or off, dimming a light, or changing any device's state -
    "recognition" here means face/voice identification of a household member, nothing else,
    despite the tool-naming family resemblance to update_devices_state/update_security_state/
    update_vision_state (all excluded from the model's own toolset - see
    tau_core.llm.toolset.MODEL_EXCLUDED_TOOLS). "Switch off the porch light"/"dim the lounge
    lamps" belong to home-assistant-mcp-server's turn_off/set_light_state, never here - a live
    eval (2026-09-11, Phase 44) found a 3b model reaching for this tool on exactly those two
    prompts, evidently pattern-matching the "update_..._state" name shape rather than the
    person-recognition intent this docstring actually describes.

    If the profile already has portrait_svg/portrait_ascii cached, pass that straight through
    unchanged; only draw a new one when neither is present (see
    memory-mcp-server__set_person_portrait to cache a freshly-drawn one for next time). At least
    one of svg or ascii_art must be provided; same e-ink convention as update_design_state (black
    strokes only, no fill/gradient, 2-3px stroke-width).
    """
    if not svg and not ascii_art:
        raise ValueError("update_recognition_state requires at least one of svg or ascii_art")
    _ui_state.recognition.update(person_id, role, svg=svg, ascii_art=ascii_art)
    _ui_state.update_timestamp()
    return json.dumps({"status": "recognition_updated", "person_id": person_id, "role": role})


@server.tool()
def clear_recognition_state() -> str:
    """Clear the recognition card early (e.g. the person left before the frontend's own
    auto-dismiss timer fired). Not needed for the common case - the frontend hides the card a
    few seconds after recognized_at on its own.
    """
    _ui_state.recognition.active = False
    _ui_state.update_timestamp()
    return json.dumps({"status": "recognition_cleared"})


@server.tool()
def refresh_approval_queue() -> str:
    """Refresh the approval queue from Phase 4 proposal store.

    Called when proposals are created, approved, or rejected to update
    the UI with current pending actions requiring user authorization.
    """
    _load_approval_queue()
    return json.dumps({
        "status": "queue_refreshed",
        "pending_count": len(_approval_queue)
    })


@server.tool()
def mark_approval_viewed(proposal_id: str) -> str:
    """Mark an approval as viewed (visual acknowledgment on frontend).

    This doesn't change the proposal status - it just updates UI state
    to indicate the user has seen the pending action.
    """
    # In a real system, you'd track view state in a separate store
    return json.dumps({
        "status": "marked_viewed",
        "proposal_id": proposal_id
    })


if __name__ == "__main__":
    server.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
