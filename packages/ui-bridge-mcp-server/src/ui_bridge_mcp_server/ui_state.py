"""Aggregated UI state from domain servers.

This module collects state from voice-mcp-server, home-assistant-mcp-server,
vision-mcp-server, and security-mcp-server, providing a unified view for the
frontend to subscribe to.
"""

import logging
import os
import tempfile
from collections import OrderedDict
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

from ui_bridge_mcp_server import crypto_store

logger = logging.getLogger(__name__)

# How many turns of transcript history to retain server-side. Older tablets poll this
# resource on a timer rather than holding a persistent connection, so the history has to
# live here (not just in one browser tab's memory) for every client to see the same log.
MAX_TRANSCRIPT_HISTORY = 200

# How many per-device buckets to retain (Phase 15 #2). `device_id` is a client-supplied opaque
# string (web/devices.py), so an unbounded dict is a memory/disk-growth vector for anything
# spraying ids - and Phase 14 now *persists* device_histories, so it grows the volume too. Mirrors
# Phase 12's SessionRegistry LRU cap (TAU_SESSION_MAX_DEVICES, same default): past the cap the
# least-recently-updated bucket is dropped; that device just starts with a fresh (empty) history
# next turn - a benign outcome, and the unified `history` still holds its recent turns for admins.
MAX_TRANSCRIPT_DEVICES = 64


@dataclass
class TranscriptEntry:
    speaker: str  # "user" | "tau"
    text: str
    timestamp: str
    device_id: str = ""  # which client this turn belongs to ("" = unattributed/legacy)


def _entry_from_dict(e: dict) -> "TranscriptEntry":
    """Rebuild a TranscriptEntry from a persisted record, tolerant of a missing device_id (older
    stores) and ignoring any unknown keys a future schema might add."""
    return TranscriptEntry(
        speaker=e["speaker"],
        text=e["text"],
        timestamp=e["timestamp"],
        device_id=e.get("device_id", ""),
    )


@dataclass
class TranscriptionState:
    """Current transcription state.

    Phase 6.D - privacy: `history` is now the **admin-only unified** log (every device's turns),
    and `device_histories` holds each device's own turns. A per-device client must be served its
    own bucket, never `history`; the bridge (web/server.py) enforces that boundary - the raw MCP
    resource here is internal and only the bridge talks to it. `update()` appends to both.

    `current_text` is kept for backward compatibility with older clients; `history` remains the
    field older callers read, so nothing that already consumed the unified log breaks.

    Phase 14 - durability: with `store_path` set, the log is persisted to disk on every update and
    reloaded on construction, so it survives a container restart. Before this it was a module-level
    global that died with the container - and Phase 6.D's privacy design leaned on it being the
    durable cross-device record, while 6.F removed kiosk scrollback on that premise, so older turns
    were retained *nowhere*. Encrypted at rest when TAU_MASTER_KEY is set (the log holds household
    conversation content), reusing the same crypto_store as the approval/proposal stores;
    plaintext otherwise. `store_path=None` (the default) keeps the pure in-memory behaviour tests
    and any embedding caller rely on.
    """

    active: bool = False
    current_text: str = ""
    updated_at: str = ""
    history: list = field(default_factory=list)  # unified (all devices) - admin-only
    # device_id -> list[TranscriptEntry], LRU-ordered (most-recently-updated last) so the
    # least-active device is the one evicted past max_devices (Phase 15 #2).
    device_histories: "OrderedDict[str, list]" = field(default_factory=OrderedDict)
    store_path: str | None = None  # Phase 14: durable persistence (None = in-memory only)
    max_devices: int = MAX_TRANSCRIPT_DEVICES  # Phase 15 #2: LRU cap on the number of buckets

    def __post_init__(self):
        if self.max_devices < 1:
            raise ValueError("max_devices must be at least 1")
        # Tolerate a plain dict passed in (tests, or a caller that didn't use the default) - the
        # LRU logic below needs OrderedDict's move_to_end/popitem.
        if not isinstance(self.device_histories, OrderedDict):
            self.device_histories = OrderedDict(self.device_histories)
        if self.store_path:
            self._load()

    def update(self, text: str, speaker: str = "tau", device_id: str = ""):
        self.current_text = text
        self.updated_at = datetime.utcnow().isoformat()
        entry = TranscriptEntry(
            speaker=speaker, text=text, timestamp=self.updated_at, device_id=device_id or ""
        )
        self.history.append(entry)
        if len(self.history) > MAX_TRANSCRIPT_HISTORY:
            self.history = self.history[-MAX_TRANSCRIPT_HISTORY:]
        if device_id:
            bucket = self.device_histories.get(device_id)
            if bucket is None:
                bucket = []
                self.device_histories[device_id] = bucket
            self.device_histories.move_to_end(device_id)  # touched = most-recently-used
            bucket.append(entry)
            if len(bucket) > MAX_TRANSCRIPT_HISTORY:
                self.device_histories[device_id] = bucket[-MAX_TRANSCRIPT_HISTORY:]
            # Evict the least-recently-updated bucket(s) past the cap (front = LRU).
            while len(self.device_histories) > self.max_devices:
                self.device_histories.popitem(last=False)
        self._persist()

    def _load(self) -> None:
        """Reload the persisted log on construction. A corrupt or undecryptable store must not
        stop the server from booting - start empty rather than crash (same posture as tau-core's
        approval-queue load), because a lost transcript is recoverable and a dead bridge is not."""
        path = Path(self.store_path)
        if not path.exists():
            return
        try:
            key = crypto_store.resolve_key(path)
            data = crypto_store.read_json(path, key)
            self.history = [_entry_from_dict(e) for e in data.get("history", [])]
            # Preserve persisted order (LRU: least-recently-updated first) and re-apply the cap in
            # case the store was written by an older, uncapped build (Phase 15 #2) - keep the most
            # recent `max_devices` buckets, dropping the stale front.
            buckets = OrderedDict(
                (dev, [_entry_from_dict(e) for e in entries])
                for dev, entries in data.get("device_histories", {}).items()
            )
            while len(buckets) > self.max_devices:
                buckets.popitem(last=False)
            self.device_histories = buckets
        except (OSError, ValueError, TypeError, KeyError):
            logger.warning(
                "Could not load transcript store %s; starting with an empty log", path, exc_info=True
            )

    def _persist(self) -> None:
        """Best-effort atomic write of the whole log. A persistence failure must never take a
        chat turn's transcript update down with it - it's a display/record concern, not the
        conversation itself - so we log and continue in-memory."""
        if not self.store_path:
            return
        path = Path(self.store_path)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            key = crypto_store.resolve_key(path)
            data = {
                "history": [asdict(e) for e in self.history],
                "device_histories": {
                    dev: [asdict(e) for e in entries] for dev, entries in self.device_histories.items()
                },
            }
            payload = crypto_store.write_json_bytes(data, key)
            # Unique temp name in the same dir, then os.replace (Phase 15 #4). A fixed "<store>.tmp"
            # is safe only under ui-bridge's single-process synchronous update(); two concurrent
            # writers (multiple workers, or a future async write path) would collide on the one temp
            # path and could corrupt a write. Mirrors tau-core's approval-queue _save_locked.
            with tempfile.NamedTemporaryFile(
                "wb", dir=path.parent, prefix=path.name, suffix=".tmp", delete=False
            ) as handle:
                handle.write(payload)
                temp_name = handle.name
            os.replace(temp_name, path)  # atomic: a crash mid-write can't corrupt the live store
        except OSError:
            logger.warning("Could not persist transcript to %s; continuing in-memory", path, exc_info=True)

    def device_history(self, device_id: str) -> list:
        """One device's own turns (empty for an unknown/absent id - fail toward showing nothing,
        never toward leaking another device's conversation)."""
        if not device_id:
            return []
        return self.device_histories.get(device_id, [])

    def public_dict(self) -> dict:
        """The device-neutral view exposed through the general ui://state resource. It carries
        NO conversation history - neither the unified log nor any device's bucket - so the
        bridge's generic resource passthrough can't leak one device's chat to another. The
        bridge fills in the requesting device's own history (via get_device_transcript) when it
        serves /api/state."""
        return {
            "active": self.active,
            "current_text": self.current_text,
            "updated_at": self.updated_at,
            "history": [],  # filled per-device by the bridge; never the unified log
        }


@dataclass
class SecurityState:
    """Current security state from security-mcp-server."""

    lockdown_active: bool = False
    lockdown_reason: str = ""
    intrusion_count: int = 0
    last_incident: str = ""


@dataclass
class DeviceState:
    """Device states from home-assistant-mcp-server."""

    device_count: int = 0
    online_count: int = 0
    active_automations: list = None

    def __post_init__(self):
        if self.active_automations is None:
            self.active_automations = []


@dataclass
class VisionState:
    """Camera/vision state from vision-mcp-server."""

    camera_active: bool = False
    current_snapshot: str = ""  # base64-encoded image
    scene_description: str = ""


@dataclass
class DesignState:
    """Current engineering design/sketch Tau has drawn, e-ink technical-drawing style.

    Populated when the user asks Tau to design/sketch something (circuit, bracket, wiring
    diagram, etc). `svg` is the primary render path - a self-contained <svg> fragment using
    only strokes (no fills/gradients) so it matches the atom's aesthetic. `ascii_art` is a
    fallback for clients that can't or don't want to render SVG (e.g. a constrained kiosk
    browser). Both are optional; the frontend renders whichever is present, preferring svg.
    """

    active: bool = False
    title: str = ""
    description: str = ""
    svg: str = ""
    ascii_art: str = ""
    updated_at: str = ""

    def update(self, title: str, description: str, svg: str = "", ascii_art: str = ""):
        self.active = True
        self.title = title
        self.description = description
        self.svg = svg
        self.ascii_art = ascii_art
        self.updated_at = datetime.utcnow().isoformat()


@dataclass
class RecognitionState:
    """Most recent face-recognition event, rendered as an e-ink "USER RECOGNIZED" portrait
    card by the frontend.

    Populated via update_recognition_state when Tau identifies a known person (typically
    memory-mcp-server__match_face followed by memory-mcp-server__get_person_profile). `svg`/
    `ascii_art` follow the same e-ink convention as DesignState (black strokes only, no
    fill/gradient) so a portrait cached on the person's profile
    (memory-mcp-server__set_person_portrait) and a freshly-drawn one render identically. This
    is a transient event, not a persistent panel - the frontend auto-dismisses the card a few
    seconds after `recognized_at`, so nothing needs to explicitly clear it for the common case
    (clear_recognition_state exists for the person-left-early case).
    """

    active: bool = False
    person_id: str = ""
    role: str = ""
    svg: str = ""
    ascii_art: str = ""
    recognized_at: str = ""

    def update(self, person_id: str, role: str, svg: str = "", ascii_art: str = ""):
        self.active = True
        self.person_id = person_id
        self.role = role
        self.svg = svg
        self.ascii_art = ascii_art
        self.recognized_at = datetime.utcnow().isoformat()


@dataclass
class UIState:
    """Unified UI state aggregating all domains."""

    transcription: TranscriptionState = None
    security: SecurityState = None
    devices: DeviceState = None
    vision: VisionState = None
    design: DesignState = None
    recognition: RecognitionState = None
    last_update: str = ""

    def __post_init__(self):
        if self.transcription is None:
            self.transcription = TranscriptionState()
        if self.security is None:
            self.security = SecurityState()
        if self.devices is None:
            self.devices = DeviceState()
        if self.vision is None:
            self.vision = VisionState()
        if self.design is None:
            self.design = DesignState()
        if self.recognition is None:
            self.recognition = RecognitionState()
        self.update_timestamp()

    def update_timestamp(self):
        self.last_update = datetime.utcnow().isoformat()

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            # Device-neutral transcription only (no history) - see TranscriptionState.public_dict.
            "transcription": self.transcription.public_dict(),
            "security": asdict(self.security),
            "devices": asdict(self.devices),
            "vision": asdict(self.vision),
            "design": asdict(self.design),
            "recognition": asdict(self.recognition),
            "last_update": self.last_update,
        }
