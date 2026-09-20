"""Tests for the runtime-switchable TTS voice registry.

Ported from test_wake_word.py's switchable-wake-word suite, because tts_voice.py is a deliberate
mirror of wake_word.py and the two should fail the same way when either breaks. One case here has
no wake-word equivalent and should: the empty-string env var, which is what docker-compose actually
passes for an unset variable and which the `or`-not-a-dict-default idiom exists to survive.
"""

from __future__ import annotations

import json

import pytest

from voice_mcp_server import tts_voice
from voice_mcp_server.tts_voice import (
    AVAILABLE_VOICES,
    DEFAULT_TTS_VOICE,
    TtsVoiceSelector,
)


@pytest.fixture(autouse=True)
def _isolated_state_path(tmp_path, monkeypatch):
    """Point persistence at tmp_path for every test in this file.

    Without this, a set_voice() anywhere in the suite writes to the real /app/data path (or
    whatever TTS_VOICE_STATE_PATH happens to be set to on the machine running the tests) and the
    tests start leaking into each other and into the developer's box.
    """
    monkeypatch.setattr(tts_voice, "_STATE_PATH", tmp_path / "tts_voice_selection.json")
    monkeypatch.delenv("PIPER_VOICE", raising=False)


def test_default_voice_when_nothing_is_configured():
    assert TtsVoiceSelector().voice == DEFAULT_TTS_VOICE


def test_the_default_is_one_of_the_offered_voices():
    """DEFAULT_TTS_VOICE also has to match docker-compose's piper --voice flag, since that is the
    one model wyoming-piper downloads unprompted. If it drifts out of the registry, the fallback
    path in speak() would point at something the picker cannot even name."""
    assert DEFAULT_TTS_VOICE in AVAILABLE_VOICES


def test_env_var_sets_the_first_run_default(monkeypatch):
    monkeypatch.setenv("PIPER_VOICE", "en_US-amy-medium")
    assert TtsVoiceSelector().voice == "en_US-amy-medium"


def test_empty_env_var_falls_through_to_the_default(monkeypatch):
    """docker-compose passes `${PIPER_VOICE:-}` as an EMPTY STRING, not as a missing key. A dict
    default would hand back "" here and we would synthesize against a voice named "". This is the
    exact trap wake_word.py documents, and the reason both modules use `or`."""
    monkeypatch.setenv("PIPER_VOICE", "")
    assert TtsVoiceSelector().voice == DEFAULT_TTS_VOICE


def test_explicit_argument_beats_everything(monkeypatch):
    monkeypatch.setenv("PIPER_VOICE", "en_US-amy-medium")
    assert TtsVoiceSelector(voice="en_GB-alba-medium").voice == "en_GB-alba-medium"


def test_set_voice_persists_and_survives_a_new_instance():
    TtsVoiceSelector().set_voice("en_US-ryan-high")
    assert TtsVoiceSelector().voice == "en_US-ryan-high"


def test_a_persisted_choice_outranks_a_stale_env_var(monkeypatch):
    """The whole point of persisting. PIPER_VOICE only sets the first-run default; once someone
    picks a voice in the UI, a redeploy that still carries the old env var must not silently
    revert them."""
    TtsVoiceSelector().set_voice("en_GB-alba-medium")
    monkeypatch.setenv("PIPER_VOICE", "en_US-amy-medium")
    assert TtsVoiceSelector().voice == "en_GB-alba-medium"


def test_corrupt_state_file_falls_back_rather_than_crashing():
    tts_voice._STATE_PATH.write_text("{not json")
    assert TtsVoiceSelector().voice == DEFAULT_TTS_VOICE


def test_a_voice_dropped_from_the_registry_does_not_resurrect():
    """An old state file naming a voice a later release removed must not win. Re-validating on read
    is what stops a stale file from selecting something that no longer exists."""
    tts_voice._STATE_PATH.write_text(json.dumps({"voice_id": "en_US-retired-medium"}))
    assert TtsVoiceSelector().voice == DEFAULT_TTS_VOICE


def test_unknown_voice_is_rejected():
    with pytest.raises(ValueError, match="unknown voice"):
        TtsVoiceSelector().set_voice("klingon-basso-profundo")


def test_listing_flags_the_current_voice():
    selector = TtsVoiceSelector()
    selector.set_voice("en_US-amy-medium")
    listing = selector.list_available()
    current = [row for row in listing if row["current"]]
    assert len(current) == 1
    assert current[0]["id"] == "en_US-amy-medium"
    assert len(listing) == len(AVAILABLE_VOICES)


def test_listing_marks_which_models_are_actually_installed():
    listing = TtsVoiceSelector().list_available(installed={"en_US-lessac-medium"})
    by_id = {row["id"]: row["available"] for row in listing}
    assert by_id["en_US-lessac-medium"] is True
    assert by_id["en_US-amy-medium"] is False


def test_a_failed_probe_reports_everything_available_rather_than_nothing():
    """installed=None means the probe failed, not that Piper is empty. Greying out every voice
    because TTS happened to be restarting would be a worse lie than an optimistic row."""
    listing = TtsVoiceSelector().list_available(installed=None)
    assert all(row["available"] for row in listing)


def test_every_registry_entry_has_a_label_and_a_download_path():
    """hf_dir is what the compose seed service pulls from. A voice offered in the picker with no
    download path is a voice that can never actually work."""
    for voice_id, info in AVAILABLE_VOICES.items():
        assert info["label"], voice_id
        assert info["hf_dir"].count("/") == 3, f"{voice_id}: expected lang/locale/name/quality"
