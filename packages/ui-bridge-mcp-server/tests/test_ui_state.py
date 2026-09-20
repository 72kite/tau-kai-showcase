import pytest
from ui_bridge_mcp_server.ui_state import (
    UIState,
    TranscriptionState,
    SecurityState,
    DeviceState,
    VisionState,
    DesignState,
    RecognitionState,
    MAX_TRANSCRIPT_HISTORY,
    MAX_TRANSCRIPT_DEVICES,
)


def test_ui_state_initializes_with_defaults():
    """UIState starts with empty/safe defaults."""
    state = UIState()

    assert state.transcription.active is False
    assert state.security.lockdown_active is False
    assert state.devices.device_count == 0
    assert state.vision.camera_active is False


def test_transcription_state_update():
    """TranscriptionState can be updated."""
    ts = TranscriptionState()
    ts.update("Hello, world")

    assert ts.current_text == "Hello, world"
    assert ts.updated_at != ""


def test_ui_state_to_dict():
    """UIState.to_dict() serializes to JSON-compatible dict."""
    state = UIState()
    state.transcription.active = True
    state.transcription.update("Test")

    d = state.to_dict()

    assert d["transcription"]["active"] is True
    assert d["transcription"]["current_text"] == "Test"
    assert "last_update" in d


def test_security_state_lockdown():
    """SecurityState tracks lockdown."""
    ss = SecurityState()
    assert ss.lockdown_active is False

    ss.lockdown_active = True
    ss.lockdown_reason = "Intrusion detected"
    assert ss.lockdown_reason == "Intrusion detected"


def test_device_state_counts():
    """DeviceState tracks device counts."""
    ds = DeviceState(device_count=5, online_count=4)

    assert ds.device_count == 5
    assert ds.online_count == 4


def test_vision_state_snapshot():
    """VisionState can store snapshot data."""
    vs = VisionState()
    vs.camera_active = True
    vs.current_snapshot = "base64_encoded_image_data"

    assert vs.camera_active is True
    assert vs.current_snapshot == "base64_encoded_image_data"


def test_transcription_state_accumulates_history_with_speaker():
    """Each update() call appends a speaker-tagged entry rather than overwriting one line -
    the frontend's always-visible transcript zone depends on the full history being present.
    """
    ts = TranscriptionState()
    ts.update("design a bracket for me", speaker="user")
    ts.update("Sketching a mounting bracket now.", speaker="tau")

    assert len(ts.history) == 2
    assert ts.history[0].speaker == "user"
    assert ts.history[0].text == "design a bracket for me"
    assert ts.history[1].speaker == "tau"
    assert ts.current_text == "Sketching a mounting bracket now."


def test_transcription_history_caps_at_max():
    """History is bounded so the resource payload can't grow unbounded over a long session."""
    ts = TranscriptionState()
    for i in range(MAX_TRANSCRIPT_HISTORY + 10):
        ts.update(f"line {i}", speaker="user")

    assert len(ts.history) == MAX_TRANSCRIPT_HISTORY
    assert ts.history[-1].text == f"line {MAX_TRANSCRIPT_HISTORY + 9}"


# --- Phase 14: durable transcript (§10.2 #6) ---------------------------------------------------


def test_transcript_persists_and_reloads_across_instances(tmp_path):
    """The whole point: a new TranscriptionState pointed at the same store recovers the log a
    previous one wrote - so a container restart no longer discards every past turn."""
    store = tmp_path / "transcript.json"

    ts = TranscriptionState(store_path=str(store))
    ts.update("secret from A", speaker="user", device_id="devA")
    ts.update("reply to A", speaker="tau", device_id="devA")
    ts.update("secret from B", speaker="user", device_id="devB")

    reloaded = TranscriptionState(store_path=str(store))
    assert [e.text for e in reloaded.history] == ["secret from A", "reply to A", "secret from B"]
    # Per-device buckets survive too (6.D isolation must hold across a restart, not just in RAM).
    assert [e.text for e in reloaded.device_histories["devA"]] == ["secret from A", "reply to A"]
    assert [e.text for e in reloaded.device_histories["devB"]] == ["secret from B"]


def test_in_memory_when_no_store_path(tmp_path):
    """No path = pure in-memory (the default every existing caller and test relies on): nothing
    is written to disk."""
    ts = TranscriptionState()
    ts.update("ephemeral")
    assert list(tmp_path.iterdir()) == []


def test_transcript_encrypted_at_rest_with_master_key(tmp_path, monkeypatch):
    """With TAU_MASTER_KEY set the store is ciphertext on disk (the log holds conversation
    content), but still round-trips through a key-holding reader."""
    monkeypatch.setenv("TAU_MASTER_KEY", "correct horse battery staple")
    store = tmp_path / "transcript.json"

    ts = TranscriptionState(store_path=str(store))
    ts.update("household conversation", speaker="user", device_id="devA")

    raw = store.read_bytes()
    assert b"household conversation" not in raw  # not sitting in plaintext on the volume
    reloaded = TranscriptionState(store_path=str(store))
    assert [e.text for e in reloaded.history] == ["household conversation"]


def test_corrupt_store_degrades_to_empty_not_a_crash(tmp_path):
    """A corrupt/undecryptable store must not stop the bridge booting - start empty, like the
    approval-queue load. A dead bridge is worse than a lost transcript."""
    store = tmp_path / "transcript.json"
    store.write_text("this is not valid json{", encoding="utf-8")

    ts = TranscriptionState(store_path=str(store))  # must not raise
    assert ts.history == []


def test_persist_writes_atomically_leaving_no_tmp(tmp_path):
    store = tmp_path / "transcript.json"
    ts = TranscriptionState(store_path=str(store))
    ts.update("line")
    # Phase 15 #4: the temp file now has a unique name (not a fixed <store>.tmp), so assert no
    # ".tmp" is left behind at all rather than just the old fixed name - os.replace consumes it.
    assert list(tmp_path.glob("*.tmp")) == []
    assert store.exists()


# --- Phase 15 #2: LRU cap on the number of per-device buckets ---------------------------------


def test_device_buckets_are_lru_capped(tmp_path):
    """An unbounded device_histories is a memory/disk-growth vector (device_id is client-supplied).
    Past max_devices the least-recently-updated bucket is dropped."""
    ts = TranscriptionState(max_devices=3)
    for dev in ("a", "b", "c", "d"):
        ts.update("hi", device_id=dev)

    assert list(ts.device_histories.keys()) == ["b", "c", "d"]  # "a" (oldest) evicted
    assert len(ts.device_histories) == 3


def test_touching_a_device_spares_it_from_eviction(tmp_path):
    """Recency is by last update, not first seen: a device that keeps talking must not be evicted
    ahead of one that went quiet."""
    ts = TranscriptionState(max_devices=3)
    for dev in ("a", "b", "c"):
        ts.update("hi", device_id=dev)
    ts.update("still here", device_id="a")  # "a" is now most-recently-used
    ts.update("new", device_id="d")  # forces one eviction

    assert "a" in ts.device_histories  # spared
    assert "b" not in ts.device_histories  # the now-oldest was evicted instead
    assert list(ts.device_histories.keys()) == ["c", "a", "d"]


def test_device_cap_is_enforced_on_load_from_an_older_uncapped_store(tmp_path):
    """A store written by a pre-cap build could hold unbounded buckets; loading it must re-apply
    the cap rather than faithfully restoring the unbounded growth."""
    store = tmp_path / "transcript.json"
    uncapped = TranscriptionState(store_path=str(store), max_devices=1000)
    for dev in ("a", "b", "c", "d", "e"):
        uncapped.update("hi", device_id=dev)

    reloaded = TranscriptionState(store_path=str(store), max_devices=2)
    assert list(reloaded.device_histories.keys()) == ["d", "e"]  # kept the 2 most recent


def test_default_device_cap_is_the_module_constant():
    ts = TranscriptionState()
    assert ts.max_devices == MAX_TRANSCRIPT_DEVICES


def test_max_devices_must_be_at_least_one():
    with pytest.raises(ValueError):
        TranscriptionState(max_devices=0)


def test_design_state_starts_inactive():
    """DesignState defaults to inactive/empty - the frontend hides the drawing panel."""
    ds = DesignState()
    assert ds.active is False
    assert ds.svg == ""
    assert ds.ascii_art == ""


def test_design_state_update_activates():
    """update() marks the design active and records both svg and ascii_art forms."""
    ds = DesignState()
    ds.update("Mounting bracket", "L-bracket, 40x40x3mm", svg="<svg></svg>", ascii_art="+--+")

    assert ds.active is True
    assert ds.title == "Mounting bracket"
    assert ds.svg == "<svg></svg>"
    assert ds.ascii_art == "+--+"
    assert ds.updated_at != ""


def test_ui_state_to_dict_includes_design():
    """UIState.to_dict() serializes the design sub-state alongside the others."""
    state = UIState()
    state.design.update("Test part", "desc", svg="<svg/>")

    d = state.to_dict()

    assert d["design"]["active"] is True
    assert d["design"]["title"] == "Test part"


def test_recognition_state_starts_inactive():
    """RecognitionState defaults to inactive/empty - the frontend shows no portrait card."""
    rs = RecognitionState()
    assert rs.active is False
    assert rs.person_id == ""
    assert rs.svg == ""
    assert rs.ascii_art == ""


def test_recognition_state_update_activates():
    """update() marks recognition active and records person_id/role plus both drawing forms."""
    rs = RecognitionState()
    rs.update("zion", "owner", svg="<svg></svg>", ascii_art="(o_o)")

    assert rs.active is True
    assert rs.person_id == "zion"
    assert rs.role == "owner"
    assert rs.svg == "<svg></svg>"
    assert rs.ascii_art == "(o_o)"
    assert rs.recognized_at != ""


def test_ui_state_to_dict_includes_recognition():
    """UIState.to_dict() serializes the recognition sub-state alongside the others."""
    state = UIState()
    state.recognition.update("zion", "owner", svg="<svg/>")

    d = state.to_dict()

    assert d["recognition"]["active"] is True
    assert d["recognition"]["person_id"] == "zion"
    assert d["recognition"]["role"] == "owner"
