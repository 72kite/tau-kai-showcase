"""Voice MCP server (Phase 2 of project-tau-plan.md, Section 5.3): Piper TTS + faster-whisper STT.

enroll_voiceprint/identify_speaker now implemented (Phase 2 polish):
- Compute voice embeddings via speechbrain speaker recognition model
- Store embeddings in memory-mcp-server (gated by approval via CDG)
- Search for similar voices to identify speakers

Run directly for manual testing (requires real Whisper/Piper services - see ../.env.example):
    python -m voice_mcp_server.server
"""

from __future__ import annotations

import base64
import json
import logging
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from voice_mcp_server.piper_client import PiperClient
from voice_mcp_server.tts_voice import TtsVoiceSelector
from voice_mcp_server.whisper_client import WhisperClient
from voice_mcp_server.voice_embedding import VoiceEmbedder
from voice_mcp_server.wake_word import WakeWordDetector

load_dotenv()

logger = logging.getLogger(__name__)

mcp = FastMCP(
    "voice",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)
_voice_embedder = None
_wake_detector = None
_voice_selector = None


def _embedder() -> VoiceEmbedder:
    global _voice_embedder
    if _voice_embedder is None:
        _voice_embedder = VoiceEmbedder()
    return _voice_embedder


def _wake() -> WakeWordDetector:
    global _wake_detector
    if _wake_detector is None:
        _wake_detector = WakeWordDetector()
    return _wake_detector


def _voices() -> TtsVoiceSelector:
    global _voice_selector
    if _voice_selector is None:
        _voice_selector = TtsVoiceSelector()
    return _voice_selector


def _whisper() -> WhisperClient:
    base_url = os.environ.get("WHISPER_URL")
    if not base_url:
        raise RuntimeError("WHISPER_URL must be set (see .env.example) to reach the Whisper service")
    return WhisperClient(base_url)


def _piper() -> PiperClient:
    uri = os.environ.get("PIPER_URI")
    if not uri:
        raise RuntimeError("PIPER_URI must be set (see .env.example) to reach the Piper service")
    return PiperClient(uri)


@mcp.tool()
async def speak(text: str) -> dict:
    """Synthesize speech from text via Piper. Returns base64-encoded WAV audio."""
    client, chosen = _piper(), _voices().voice
    try:
        audio = await client.synthesize(text, voice=chosen)
    except Exception:  # noqa: BLE001 - any synthesis failure retries once, unvoiced; see below
        # Piper has no model for `chosen` - never seeded, or the volume was wiped. Retry with no
        # voice at all so Piper falls back to its own --voice startup model. A missing OPTIONAL
        # voice must degrade to "Tau still talks, in the default voice", never to silence: speaking
        # is best-effort by contract all the way up to /api/voice/speak, and the text reply has
        # already been delivered by the time we get here. Logged at warning because reaching this
        # branch means the seed step did not do its job.
        logger.warning("Piper could not synthesize with voice %r; retrying with its default", chosen)
        audio = await client.synthesize(text)
    return {"audio_base64": base64.b64encode(audio).decode("ascii"), "format": "wav"}


@mcp.tool()
async def transcribe(audio_base64: str, filename: str = "audio.wav") -> str:
    """Transcribe base64-encoded audio to text via Whisper."""
    client = _whisper()
    try:
        return await client.transcribe(base64.b64decode(audio_base64), filename)
    finally:
        await client.aclose()


@mcp.tool()
def enroll_voiceprint(person_id: str, audio_base64: str) -> str:
    """Enroll a person's voice for speaker identification. Requires human approval.

    Computes a voice embedding from audio and stores it in memory-mcp-server.
    Multiple samples per person are supported (better recognition with variety).

    Returns the embedding_id from memory-mcp-server's store_voice.
    """
    embedder = _embedder()
    try:
        embedding = embedder.compute_embedding_from_b64(audio_base64)
        return json.dumps({
            "person_id": person_id,
            "embedding": embedding,
            "embedding_dim": len(embedding),
            "message": "Voice enrolled. Pass this embedding to memory-mcp-server.store_voice()"
        })
    except Exception as e:
        raise RuntimeError(f"Voice enrollment failed: {e}")


@mcp.tool()
def identify_speaker(audio_base64: str, threshold: float = 0.7) -> str:
    """Identify who is speaking by comparing against enrolled voiceprints.

    This is a read-only operation (queries memory-mcp-server, no modification).
    Computes embedding and searches for similar voices. Returns matches above threshold.

    threshold: 0-1, higher = stricter matching (default 0.7 = 70% similarity).
    """
    embedder = _embedder()
    try:
        embedding = embedder.compute_embedding_from_b64(audio_base64)
        return json.dumps({
            "embedding": embedding,
            "embedding_dim": len(embedding),
            "message": "Pass this embedding to memory-mcp-server.match_voice()"
        })
    except Exception as e:
        raise RuntimeError(f"Speaker identification failed: {e}")


@mcp.tool()
def detect_wake_word(audio_base64: str) -> str:
    """Detect the wake word in a short audio window via openWakeWord (fully local, on-device).

    Read-only perception - no actuation, no approval. Intended to be polled with rolling ~1-2s
    windows so the fully-local voice mode gets hands-free "hey ..." invoke without any cloud
    recognizer. Returns JSON {detected, score, model, threshold}.
    """
    result = _wake().detect(base64.b64decode(audio_base64))
    return json.dumps(result)


@mcp.tool()
def list_wake_words() -> str:
    """List the wake words available to switch to (openWakeWord's bundled detectors plus this
    project's own trained "hey tau"), each flagged with whether it's the one currently active.

    Read-only, no approval. Returns JSON: [{id, label, current}, ...].
    """
    return json.dumps(_wake().list_available())


@mcp.tool()
def set_wake_word(wake_id: str) -> str:
    """Switch the active local wake word to one of list_wake_words()'s ids. Takes effect on the
    next detect_wake_word poll (no restart) and persists across restarts/redeploys.

    This changes what every device listening in local voice mode hears as *the* wake phrase, so
    unlike detect_wake_word it is gated by the CDG rather than default-allow (see the CDG policy
    module for the tier this falls under) - not destructive, but a shared, system-wide setting
    change, not a private read.
    """
    try:
        return json.dumps(_wake().set_model(wake_id))
    except ValueError as e:
        raise RuntimeError(str(e))


@mcp.tool()
async def list_voices() -> str:
    """List the speaking voices available to switch Tau to, each flagged with whether it is the one
    currently active and whether the TTS service actually has that voice installed.

    Read-only, no approval. Returns JSON: [{id, label, current, available}, ...]. An entry with
    available=false will still speak if selected, but in the default voice rather than the one
    named - the model for it was never downloaded.
    """
    return json.dumps(_voices().list_available(await _piper().installed_voices()))


@mcp.tool()
def set_voice(voice_id: str) -> str:
    """Switch the voice Tau speaks in to one of list_voices()'s ids. Takes effect on the next reply
    spoken aloud (no restart) and persists across restarts/redeploys.

    This changes how Tau sounds on every device in the household, so like set_wake_word it is gated
    by the CDG rather than default-allow - not destructive, but a shared, system-wide setting rather
    than a private read.
    """
    try:
        return json.dumps(_voices().set_voice(voice_id))
    except ValueError as e:
        raise RuntimeError(str(e))


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
