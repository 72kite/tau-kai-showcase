from __future__ import annotations

import asyncio
import io
import wave

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncClient
from wyoming.info import Describe, Info
from wyoming.tts import Synthesize, SynthesizeVoice

# A Describe/Info round trip is a capability probe, not the speech path - if Piper is wedged, the
# Settings page must not hang waiting for it. Short on purpose.
_INFO_TIMEOUT_SECONDS = 5.0


class PiperClient:
    """Thin async wrapper around a Wyoming-protocol Piper TTS service
    (infra/k3s/voice/piper-deployment.yaml). Has no knowledge of MCP or env vars.
    """

    def __init__(self, uri: str):
        self._uri = uri

    async def synthesize(self, text: str, voice: str | None = None) -> bytes:
        """Sends `text` to Piper and returns the synthesized speech as WAV bytes.

        `voice` is a Piper voice name (`en_US-amy-medium`). None means "whatever Piper was started
        with" and produces byte-identical wire output to before this parameter existed - Synthesize
        omits the key entirely rather than sending a null - which is what keeps the pre-existing
        wire-format test meaningful as a compatibility check.
        """
        request = Synthesize(text=text, voice=SynthesizeVoice(name=voice) if voice else None)
        async with AsyncClient.from_uri(self._uri) as client:
            await client.write_event(request.event())

            buffer = io.BytesIO()
            wav_writer: wave.Wave_write | None = None
            try:
                while True:
                    event = await client.read_event()
                    if event is None:
                        raise RuntimeError("Piper connection closed before audio-stop")

                    if AudioStart.is_type(event.type):
                        start = AudioStart.from_event(event)
                        wav_writer = wave.open(buffer, "wb")
                        wav_writer.setnchannels(start.channels)
                        wav_writer.setsampwidth(start.width)
                        wav_writer.setframerate(start.rate)
                    elif AudioChunk.is_type(event.type):
                        if wav_writer is None:
                            raise RuntimeError("Received audio-chunk before audio-start")
                        wav_writer.writeframes(AudioChunk.from_event(event).audio)
                    elif AudioStop.is_type(event.type):
                        break
            finally:
                if wav_writer is not None:
                    wav_writer.close()

            return buffer.getvalue()

    async def installed_voices(self) -> set[str] | None:
        """Which voice models Piper actually has on disk, via a Wyoming Describe/Info round trip.

        Returns None - not an empty set - when the probe fails for any reason. The distinction
        matters to the caller: an empty set means "Piper answered, and has nothing", while None
        means "we don't know", and the Settings picker renders those two very differently. Failing
        closed here would grey out every voice whenever TTS happened to be restarting.
        """
        try:
            async with asyncio.timeout(_INFO_TIMEOUT_SECONDS):
                async with AsyncClient.from_uri(self._uri) as client:
                    await client.write_event(Describe().event())
                    while True:
                        event = await client.read_event()
                        if event is None:
                            return None  # closed before telling us anything
                        if Info.is_type(event.type):
                            info = Info.from_event(event)
                            return {
                                voice.name
                                for program in info.tts
                                for voice in program.voices
                                if voice.installed
                            }
        except Exception:  # noqa: BLE001 - any failure here means "unknown", never a broken page
            return None
