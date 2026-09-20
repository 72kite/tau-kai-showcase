"""Local wake-word detection via openWakeWord (fully offline, MIT-licensed).

This closes the last piece of the fully-local voice path (project-tau-plan.md Section 10): "hey
tau" hands-free invoke currently only works in VITE_VOICE_MODE=browser, which rides Chrome's
CLOUD recognizer; the recommended local mode is double-tap-only. openWakeWord runs a small ONNX
model on-device, so the wake word never leaves the LAN - matching the rest of this project's
local-first stance.

Design notes:
- Detection is READ-ONLY perception (no actuation) and falls through the CDG's default-allow, so
  it needs no approval. For consistency with transcribe/identify it's exposed as an MCP tool the
  bridge reaches via host.call_tool. Honest caveat: it's polled at a high cadence (a rolling
  window every ~1-2s), so it adds a steady trickle to the audit ring buffer; if that noise
  becomes a problem the clean fix is a dedicated standalone service (the infra/whisper pattern)
  the frontend hits directly, off the MCP path entirely. Left as a documented follow-up.
- **A trained "hey tau" model now ships with this package** (`models/hey_tau.onnx`, 209KB) and is
  the default. It was trained locally with openWakeWord's pipeline on 50k synthetic positives per
  pronunciation plus 50k phoneme-neighbour negatives, augmented with 270 room impulse responses
  and real background noise, against 2000h of ACAV100M negative features. See the README for the
  full recipe and the measured operating curve.
- **Read the honest numbers before enabling hands-free.** At the shipped default threshold (0.5)
  it recovers ~36% of utterances with ~0.37 false wakes/hour. It is a real "hey tau" detector -
  and it is NOT yet good enough to be the primary hands-free path; double-tap remains the
  reliable route. Two spellings are in the target set ("hey tau" -> /tˈaʊ/ and "hey taw" ->
  /tˈɔː/) because Piper phonemizes deterministically, so a single spelling would have trained a
  model that never fires for speakers using the other vowel.
- Decode is PyAV, same stack as voice_embedding.py and the local whisper service, so the browser's
  webm/opus rolling windows decode without ffmpeg-CLI or torchaudio/torchcodec.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import numpy as np

# The "hey tau" model trained for this project, shipped alongside the code. Absolute path so it
# resolves the same whether the server runs from a checkout, an installed wheel, or the container
# (where the package lands under /app). Falls back to openWakeWord's bundled "hey_jarvis" only if
# the file is somehow missing, so a stripped install degrades to a working-but-wrong-phrase
# detector rather than crashing at first poll.
_BUNDLED_HEY_TAU = Path(__file__).parent / "models" / "hey_tau.onnx"
DEFAULT_WAKEWORD_MODEL = str(_BUNDLED_HEY_TAU) if _BUNDLED_HEY_TAU.is_file() else "hey_jarvis"

# Runtime-switchable wake words (project-tau-plan.md, wake-word customization pass 2026-09-08).
# id -> (display label, model name/path openWakeWord.Model(wakeword_models=[...]) accepts). Only
# includes openWakeWord's bundled ACTUAL wake-word detectors - confirmed live against the deployed
# container's site-packages/openwakeword/resources/models/ - not its non-wake-word demo models
# ("timer", "weather" detect those spoken words generally, not an invocation phrase) or its
# internal feature-extraction models (melspectrogram/silero_vad/embedding_model). All are bundled
# with the package (no training, no network fetch), so switching is instant. "hey tau" is the one
# actually trained for this project (see the README's recipe) and stays the default.
AVAILABLE_WAKE_WORDS: dict[str, dict[str, str]] = {
    "hey_tau": {"label": "Hey Tau", "model": str(_BUNDLED_HEY_TAU)},
    "hey_jarvis": {"label": "Hey Jarvis", "model": "hey_jarvis"},
    "alexa": {"label": "Alexa", "model": "alexa"},
    "hey_mycroft": {"label": "Hey Mycroft", "model": "hey_mycroft"},
    "hey_rhasspy": {"label": "Hey Rhasspy", "model": "hey_rhasspy"},
}

# Where a runtime `set_wake_word` choice persists so it survives a container restart/redeploy -
# WAKEWORD_MODEL (the env var) only sets the FIRST-run default; once someone picks a word through
# the UI, that choice should stick without needing to edit docker-compose. Lives on the same
# `voice-models` named volume SPKREC_MODEL_DIR already uses (/app/data), so it's untouched by
# `checkout -f` on redeploy - a plain env var can't be written back to from inside the container.
_STATE_PATH = Path(os.environ.get("WAKEWORD_STATE_PATH") or "/app/data/wake_word_selection.json")


def _read_persisted_choice() -> str | None:
    try:
        data = json.loads(_STATE_PATH.read_text())
        wake_id = data.get("wake_id")
        return wake_id if wake_id in AVAILABLE_WAKE_WORDS else None
    except (OSError, ValueError):
        return None  # no file yet, or unreadable - fall through to env/default


def _persist_choice(wake_id: str) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps({"wake_id": wake_id}))
    except OSError:
        pass  # best-effort - a read-only filesystem just means the choice won't survive a restart


class WakeWordDetector:
    """Wraps an openWakeWord model. Lazy-loads so importing this module (and running the rest of
    the voice server) never requires openWakeWord to be installed."""

    def __init__(self, model: str | None = None, threshold: float | None = None):
        # Model name (bundled with openWakeWord) or path to a custom .onnx. Defaults to this
        # package's trained "hey tau" model.
        #
        # `or` rather than a dict default on purpose: compose passes these through as
        # `${WAKEWORD_MODEL:-}`, so an unset variable arrives as an EMPTY STRING, not as a missing
        # key. os.environ.get(..., default) would hand back "" and we'd try to load a model named
        # "". Same reasoning for the threshold, where float("") would raise instead.
        #
        # Precedence: an explicit `model` constructor arg (tests) > a persisted `set_wake_word`
        # choice (an explicit runtime decision should outlive the env var it started from) >
        # WAKEWORD_MODEL env > the package default.
        persisted = _read_persisted_choice() if model is None else None
        self.wake_id = persisted
        if model is not None:
            self.model = model
        elif persisted is not None:
            self.model = AVAILABLE_WAKE_WORDS[persisted]["model"]
        else:
            self.model = os.environ.get("WAKEWORD_MODEL") or DEFAULT_WAKEWORD_MODEL
        self.threshold = (
            threshold if threshold is not None
            else float(os.environ.get("WAKEWORD_THRESHOLD") or "0.5")
        )
        self._oww = None

    def list_available(self) -> list[dict]:
        """Every wake word `set_wake_word` will accept, with the currently-active one flagged -
        for a Settings picker to render without hardcoding the choices twice."""
        return [
            {"id": wake_id, "label": info["label"], "current": self.model == info["model"]}
            for wake_id, info in AVAILABLE_WAKE_WORDS.items()
        ]

    def set_model(self, wake_id: str) -> dict:
        """Switch the active wake word. Hot-swaps (no restart needed): the next detect() call
        lazy-loads the new model, same as first startup. Persists so the choice survives a
        redeploy/restart - see _persist_choice's docstring on why an env var alone can't do that
        from inside a running container."""
        if wake_id not in AVAILABLE_WAKE_WORDS:
            raise ValueError(
                f"unknown wake word '{wake_id}' - choose one of {sorted(AVAILABLE_WAKE_WORDS)}"
            )
        self.wake_id = wake_id
        self.model = AVAILABLE_WAKE_WORDS[wake_id]["model"]
        self._oww = None  # drop the loaded model so _ensure_model rebuilds it against the new one
        _persist_choice(wake_id)
        return {"wake_id": wake_id, "label": AVAILABLE_WAKE_WORDS[wake_id]["label"], "model": self.model}

    def _ensure_model(self):
        if self._oww is not None:
            return
        try:
            from openwakeword.model import Model
        except ImportError:
            raise RuntimeError(
                "openwakeword not installed. Run: pip install openwakeword "
                "(and `python -c \"import openwakeword; openwakeword.utils.download_models()\"` "
                "once to fetch the bundled models)."
            )
        # wakeword_models accepts bundled names or custom .onnx paths. inference_framework="onnx",
        # not the "tflite" default: openWakeWord's bundled tflite_runtime build predates NumPy 2's
        # C-API and segfaults-then-raises (_ARRAY_API not found) against this image's numpy>=2 -
        # onnxruntime has no such ABI coupling, and every bundled model ships an .onnx variant too.
        self._oww = Model(wakeword_models=[self.model], inference_framework="onnx")

    @staticmethod
    def decode_to_16k_pcm16(audio_bytes: bytes) -> np.ndarray:
        """Decode any container (WAV, webm/opus, mp4) to a 16kHz mono int16 numpy array -
        the format openWakeWord.predict expects."""
        import av

        container = av.open(io.BytesIO(audio_bytes))
        resampler = av.audio.resampler.AudioResampler(format="s16", layout="mono", rate=16000)
        chunks = []
        for frame in container.decode(audio=0):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray())
        for resampled in resampler.resample(None):  # flush
            chunks.append(resampled.to_ndarray())
        if not chunks:
            raise ValueError("no audio frames decoded")
        return np.concatenate(chunks, axis=1).reshape(-1).astype(np.int16)

    def detect(self, audio_bytes: bytes) -> dict:
        """Run detection over a short audio window. Returns
        {detected: bool, score: float, model: str}. `detected` is score >= threshold."""
        self._ensure_model()
        pcm = self.decode_to_16k_pcm16(audio_bytes)
        scores = self._oww.predict(pcm)  # {model_name: score in [0,1]}
        # A window can span multiple frames; take the strongest score seen for our model.
        score = float(max(scores.values())) if scores else 0.0
        return {
            "detected": score >= self.threshold,
            "score": score,
            "model": self.model,
            "threshold": self.threshold,
        }
