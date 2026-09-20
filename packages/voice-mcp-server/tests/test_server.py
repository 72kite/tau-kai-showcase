import base64

import pytest

from voice_mcp_server import server


class FakePiperClient:
    """Records the voice it was asked for. The `voice` kwarg is NOT optional here on purpose: if
    this fake silently accepted a call without it, speak()'s fallback would swallow the resulting
    TypeError and the test would pass through the error path while appearing to test the happy one.
    """

    def __init__(self, wav_bytes: bytes):
        self._wav_bytes = wav_bytes
        self.calls: list[str | None] = []

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        self.calls.append(voice)
        return self._wav_bytes


class UnseededPiperClient(FakePiperClient):
    """Piper with no model for the requested voice: fails when given one, works without."""

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        self.calls.append(voice)
        if voice is not None:
            raise RuntimeError(f"no model for voice {voice!r}")
        return self._wav_bytes


class _SelectorStub:
    def __init__(self, voice: str):
        self.voice = voice


class FakeWhisperClient:
    def __init__(self, text: str):
        self._text = text
        self.received_audio: bytes | None = None

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        self.received_audio = audio
        return self._text

    async def aclose(self) -> None:
        pass


async def test_speak_returns_base64_encoded_audio(monkeypatch):
    monkeypatch.setattr(server, "_piper", lambda: FakePiperClient(b"RIFF...fake-wav"))

    result = await server.speak("hello tau")

    assert result["format"] == "wav"
    assert base64.b64decode(result["audio_base64"]) == b"RIFF...fake-wav"


async def test_speak_uses_the_selected_voice(monkeypatch):
    fake = FakePiperClient(b"RIFF...fake-wav")
    monkeypatch.setattr(server, "_piper", lambda: fake)
    monkeypatch.setattr(server, "_voices", lambda: _SelectorStub("en_US-amy-medium"))

    await server.speak("hello tau")

    assert fake.calls == ["en_US-amy-medium"]


async def test_speak_falls_back_to_the_default_voice_when_the_model_is_missing(monkeypatch):
    """The single most important test in the TTS-voice work. wyoming-piper only guarantees the one
    voice named in its --voice flag is on disk, so a selected-but-never-seeded voice is a real
    production state, not a hypothetical. Speaking is best-effort by contract all the way up to
    /api/voice/speak - a missing OPTIONAL voice must degrade to "Tau still talks, in the default
    voice", never to silence, and never to an exception that surfaces as a failed turn.
    """
    fake = UnseededPiperClient(b"RIFF...fake-wav")
    monkeypatch.setattr(server, "_piper", lambda: fake)
    monkeypatch.setattr(server, "_voices", lambda: _SelectorStub("en_GB-alba-medium"))

    result = await server.speak("hello tau")

    assert base64.b64decode(result["audio_base64"]) == b"RIFF...fake-wav"
    assert fake.calls == ["en_GB-alba-medium", None], "should retry once, unvoiced"


async def test_set_voice_rejects_an_unknown_id():
    with pytest.raises(RuntimeError, match="unknown voice"):
        server.set_voice("klingon-basso-profundo")


async def test_transcribe_decodes_base64_and_returns_text(monkeypatch):
    fake_client = FakeWhisperClient("turn on the kitchen light")
    monkeypatch.setattr(server, "_whisper", lambda: fake_client)

    audio_base64 = base64.b64encode(b"fake-audio-bytes").decode("ascii")
    text = await server.transcribe(audio_base64, filename="clip.wav")

    assert text == "turn on the kitchen light"
    assert fake_client.received_audio == b"fake-audio-bytes"


async def test_whisper_client_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("WHISPER_URL", raising=False)

    with pytest.raises(RuntimeError, match="WHISPER_URL"):
        server._whisper()


async def test_piper_client_missing_env_raises_clear_error(monkeypatch):
    monkeypatch.delenv("PIPER_URI", raising=False)

    with pytest.raises(RuntimeError, match="PIPER_URI"):
        server._piper()
