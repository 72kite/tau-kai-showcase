"""WakeWordDetector threshold/score logic, with openWakeWord and audio decode faked (the model
and PyAV aren't needed to prove the decision boundary)."""

import json

import numpy as np
import pytest

from voice_mcp_server import wake_word
from voice_mcp_server.wake_word import AVAILABLE_WAKE_WORDS, WakeWordDetector


class _FakeOWW:
    def __init__(self, score):
        self._score = score

    def predict(self, pcm):
        assert isinstance(pcm, np.ndarray)
        return {"hey_jarvis": self._score}


def _detector_with(score, threshold=0.5, monkeypatch=None):
    det = WakeWordDetector(model="hey_jarvis", threshold=threshold)
    det._oww = _FakeOWW(score)  # skip the real lazy load
    # skip PyAV decode; detect() only needs *an* ndarray to hand to predict()
    monkeypatch.setattr(det, "decode_to_16k_pcm16", staticmethod(lambda b: np.zeros(1600, np.int16)))
    return det


def test_score_at_or_above_threshold_detects(monkeypatch):
    det = _detector_with(0.8, threshold=0.5, monkeypatch=monkeypatch)
    out = det.detect(b"anything")
    assert out["detected"] is True
    assert out["score"] == pytest.approx(0.8)
    assert out["model"] == "hey_jarvis"
    assert out["threshold"] == 0.5


def test_score_below_threshold_does_not_detect(monkeypatch):
    det = _detector_with(0.3, threshold=0.5, monkeypatch=monkeypatch)
    assert det.detect(b"anything")["detected"] is False


def test_boundary_is_inclusive(monkeypatch):
    det = _detector_with(0.5, threshold=0.5, monkeypatch=monkeypatch)
    assert det.detect(b"anything")["detected"] is True


def test_env_configures_model_and_threshold(monkeypatch):
    monkeypatch.setenv("WAKEWORD_MODEL", "hey_mycroft")
    monkeypatch.setenv("WAKEWORD_THRESHOLD", "0.9")
    det = WakeWordDetector()
    assert det.model == "hey_mycroft"
    assert det.threshold == 0.9


def test_bundled_hey_tau_model_is_present_and_self_contained():
    """The trained model must ship inside the package.

    Guards a silent failure: if the .onnx goes missing from a build, DEFAULT_WAKEWORD_MODEL falls
    back to "hey_jarvis" and the assistant listens for the wrong phrase entirely - with no error
    anywhere. Also asserts a realistic size, because the raw torch export wrote its weights as
    external data and was only 14KB; that file loads but carries no weights.
    """
    from voice_mcp_server.wake_word import _BUNDLED_HEY_TAU, DEFAULT_WAKEWORD_MODEL

    assert _BUNDLED_HEY_TAU.is_file(), f"trained model missing at {_BUNDLED_HEY_TAU}"
    assert _BUNDLED_HEY_TAU.stat().st_size > 100_000, (
        "hey_tau.onnx is suspiciously small - weights are probably stored as external data "
        "rather than inlined, which makes the file useless on its own"
    )
    assert DEFAULT_WAKEWORD_MODEL == str(_BUNDLED_HEY_TAU)
    assert not DEFAULT_WAKEWORD_MODEL.endswith("hey_jarvis")


def test_default_model_is_used_when_env_unset(monkeypatch):
    monkeypatch.delenv("WAKEWORD_MODEL", raising=False)
    monkeypatch.delenv("WAKEWORD_THRESHOLD", raising=False)
    det = WakeWordDetector()
    assert det.model.endswith("hey_tau.onnx")
    assert det.threshold == 0.5


@pytest.fixture(autouse=True)
def _isolated_state_path(tmp_path, monkeypatch):
    """Every WakeWordDetector() call reads _STATE_PATH for a persisted set_wake_word choice - if
    that pointed at the real default (/app/data/...) here, a test calling set_model() would write
    to (and possibly create) a real path on whatever machine runs the suite, and leak state into
    every other test's WakeWordDetector() construction. Point it at a throwaway tmp_path instead,
    for every test in this file, whether or not it touches persistence directly."""
    monkeypatch.setattr(wake_word, "_STATE_PATH", tmp_path / "wake_word_selection.json")


class TestSwitchableWakeWords:
    """set_wake_word / list_wake_words (2026-09-08): runtime-switchable bundled wake words."""

    def test_list_available_flags_the_active_model(self, monkeypatch):
        monkeypatch.delenv("WAKEWORD_MODEL", raising=False)
        det = WakeWordDetector()
        listing = det.list_available()
        ids = {row["id"] for row in listing}
        assert ids == set(AVAILABLE_WAKE_WORDS)
        current = [row for row in listing if row["current"]]
        assert current == [{"id": "hey_tau", "label": "Hey Tau", "current": True}]

    def test_set_model_switches_and_hot_swaps(self):
        det = WakeWordDetector()
        det._oww = object()  # pretend a model is already loaded
        result = det.set_model("hey_jarvis")
        assert result == {"wake_id": "hey_jarvis", "label": "Hey Jarvis", "model": "hey_jarvis"}
        assert det.model == "hey_jarvis"
        # The stale loaded model must be dropped so the next detect() call rebuilds against the
        # new one - a live self._oww still pointing at the old model would silently keep
        # listening for the old wake word despite `set_model` claiming success.
        assert det._oww is None

    def test_set_model_rejects_unknown_id(self):
        det = WakeWordDetector()
        with pytest.raises(ValueError, match="unknown wake word"):
            det.set_model("not_a_real_wake_word")

    def test_set_model_persists_across_new_instances(self):
        WakeWordDetector().set_model("alexa")
        # A fresh instance (simulating a container restart) should pick the persisted choice back
        # up rather than reverting to the "hey tau" default.
        assert WakeWordDetector().model == "alexa"

    def test_persisted_choice_survives_even_with_a_stale_env_var(self, monkeypatch):
        # WAKEWORD_MODEL only sets the FIRST-run default; an explicit runtime choice made through
        # set_wake_word should win over whatever the env var says, not be silently overridden by
        # it (env vars are compose-level, harder for an admin using the Settings UI to know about
        # or change than the picker they just used).
        WakeWordDetector().set_model("hey_mycroft")
        monkeypatch.setenv("WAKEWORD_MODEL", "hey_jarvis")
        assert WakeWordDetector().model == "hey_mycroft"

    def test_corrupt_state_file_falls_back_gracefully(self, monkeypatch, tmp_path):
        state_path = tmp_path / "wake_word_selection.json"
        state_path.write_text("not json")
        monkeypatch.setattr(wake_word, "_STATE_PATH", state_path)
        monkeypatch.delenv("WAKEWORD_MODEL", raising=False)
        det = WakeWordDetector()
        assert det.model == wake_word.DEFAULT_WAKEWORD_MODEL

    def test_state_file_with_unknown_persisted_id_falls_back_gracefully(self, monkeypatch, tmp_path):
        # Defends against a future AVAILABLE_WAKE_WORDS edit removing an id someone had selected -
        # the persisted file shouldn't be able to point WakeWordDetector at a model that no
        # longer has a listing entry.
        state_path = tmp_path / "wake_word_selection.json"
        state_path.write_text(json.dumps({"wake_id": "no_longer_available"}))
        monkeypatch.setattr(wake_word, "_STATE_PATH", state_path)
        monkeypatch.delenv("WAKEWORD_MODEL", raising=False)
        det = WakeWordDetector()
        assert det.model == wake_word.DEFAULT_WAKEWORD_MODEL


def test_missing_openwakeword_raises_clear_error(monkeypatch):
    # Force the lazy import to fail and confirm the message tells the operator what to install.
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("openwakeword"):
            raise ImportError("no module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    det = WakeWordDetector()
    with pytest.raises(RuntimeError, match="openwakeword not installed"):
        det._ensure_model()
