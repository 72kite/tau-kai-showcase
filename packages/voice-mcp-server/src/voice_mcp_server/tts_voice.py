"""Runtime-switchable Piper TTS voice.

Deliberately mirrors wake_word.py - same registry shape, same persist-to-the-voice-models-volume
trick, same precedence chain - so the two runtime-switchable voice settings behave identically from
an operator's point of view. If you've read that file you've read this one.

One difference worth naming: a wake word id maps to a model path, while a TTS voice id IS the Piper
voice name. `en_US-lessac-medium` is simultaneously the compose `--voice` flag, the
SynthesizeVoice.name on the wire, the .onnx filename, and the HuggingFace directory leaf. Collapsing
them removes a whole class of mapping bug that has nowhere useful to hide.

The constraint this module does NOT solve on its own: wyoming-piper only guarantees the voice named
in its own `--voice` startup flag is present on disk. Selecting a voice nothing ever downloaded
would fail at synthesis time, so there are two other halves - docker-compose.yml's
`piper-voice-seed` service, which puts the models there, and PiperClient.installed_voices(), which
lets the picker say which ones actually arrived.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# MUST match docker-compose.yml's piper `--voice` flag. That is the one voice wyoming-piper
# downloads on its own, so it is the only one guaranteed present even if the seed service never ran
# - which makes it the correct fallback as well as the correct default.
DEFAULT_TTS_VOICE = "en_US-lessac-medium"

# Voices offered in the Settings picker. `hf_dir` is the path under rhasspy/piper-voices the seed
# service pulls from; it lives here rather than only in compose so the seed list and the selectable
# list are generated from one source and can't drift into offering a voice nobody downloaded.
AVAILABLE_VOICES: dict[str, dict[str, str]] = {
    "en_US-lessac-medium": {
        "label": "Lessac - US English, neutral",
        "hf_dir": "en/en_US/lessac/medium",
    },
    "en_US-amy-medium": {
        "label": "Amy - US English, warmer",
        "hf_dir": "en/en_US/amy/medium",
    },
    "en_US-ryan-high": {
        "label": "Ryan - US English, deeper",
        "hf_dir": "en/en_US/ryan/high",
    },
    "en_US-kristin-medium": {
        "label": "Kristin - US English, brighter",
        "hf_dir": "en/en_US/kristin/medium",
    },
    "en_GB-alba-medium": {
        "label": "Alba - UK English",
        "hf_dir": "en/en_GB/alba/medium",
    },
}

# Where a runtime `set_voice` choice persists so it survives a container restart/redeploy. Same
# reasoning as wake_word.py's _STATE_PATH, and the same volume (/app/data on `voice-models`):
# PIPER_VOICE only sets the FIRST-run default, and a process can't write back to its own
# environment, so without this file every redeploy would silently revert a choice someone made.
_STATE_PATH = Path(os.environ.get("TTS_VOICE_STATE_PATH") or "/app/data/tts_voice_selection.json")


def _read_persisted_choice() -> str | None:
    try:
        data = json.loads(_STATE_PATH.read_text())
        voice_id = data.get("voice_id")
        # Re-validated against the registry rather than trusted: a voice removed from
        # AVAILABLE_VOICES in a later release must not resurrect itself from an old state file.
        return voice_id if voice_id in AVAILABLE_VOICES else None
    except (OSError, ValueError):
        return None  # no file yet, or unreadable - fall through to env/default


def _persist_choice(voice_id: str) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps({"voice_id": voice_id}))
    except OSError:
        pass  # best-effort - a read-only filesystem just means the choice won't survive a restart


class TtsVoiceSelector:
    """Holds which Piper voice Tau currently speaks in.

    No model is loaded here and nothing is cached - the voice is sent per-request on the wire, so a
    switch takes effect on the very next speak() with no restart and no warm-up.
    """

    def __init__(self, voice: str | None = None):
        # `or` rather than a dict default on purpose, same trap as wake_word.py: compose passes
        # these through as `${PIPER_VOICE:-}`, so an unset variable arrives as an EMPTY STRING, not
        # as a missing key. os.environ.get(..., default) would hand back "" and we would synthesize
        # against a voice named "".
        #
        # Precedence: an explicit constructor arg (tests) > a persisted set_voice choice (an
        # explicit runtime decision should outlive the env var it started from) > PIPER_VOICE env >
        # the package default.
        persisted = _read_persisted_choice() if voice is None else None
        if voice is not None:
            self.voice = voice
        elif persisted is not None:
            self.voice = persisted
        else:
            self.voice = os.environ.get("PIPER_VOICE") or DEFAULT_TTS_VOICE

    def list_available(self, installed: set[str] | None = None) -> list[dict]:
        """Every voice set_voice will accept, the active one flagged, plus whether Piper actually
        has the model on disk.

        `installed=None` means the probe itself failed (Piper down, or it answered no Info event).
        Everything is then reported available rather than guessed unavailable: a probe failure is
        not evidence a voice is missing, and greying out the whole list because TTS happened to be
        restarting would be a worse lie than the occasional optimistic row.
        """
        return [
            {
                "id": voice_id,
                "label": info["label"],
                "current": self.voice == voice_id,
                "available": True if installed is None else voice_id in installed,
            }
            for voice_id, info in AVAILABLE_VOICES.items()
        ]

    def set_voice(self, voice_id: str) -> dict:
        """Switch the voice Tau speaks in. Takes effect on the next speak() - no restart, nothing to
        reload - and persists so the choice survives a redeploy."""
        if voice_id not in AVAILABLE_VOICES:
            raise ValueError(
                f"unknown voice '{voice_id}' - choose one of {sorted(AVAILABLE_VOICES)}"
            )
        self.voice = voice_id
        _persist_choice(voice_id)
        return {"voice_id": voice_id, "label": AVAILABLE_VOICES[voice_id]["label"]}
