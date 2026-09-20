"""Exercises PiperClient against a real local TCP server speaking raw Wyoming frames - no real
Piper/network dependency, but the actual wire protocol Piper uses, not a mock of our own client.
"""

import asyncio

from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.event import async_read_event, async_write_event
from wyoming.info import Attribution, Describe, Info, TtsProgram, TtsVoice
from wyoming.tts import Synthesize

from voice_mcp_server.piper_client import PiperClient

FAKE_PCM = b"\x01\x00" * 8  # 8 frames of 16-bit mono silence-ish audio


async def _fake_piper_handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    event = await async_read_event(reader)
    assert event is not None and Synthesize.is_type(event.type)

    await async_write_event(AudioStart(rate=16000, width=2, channels=1).event(), writer)
    await async_write_event(AudioChunk(audio=FAKE_PCM, rate=16000, width=2, channels=1).event(), writer)
    await async_write_event(AudioStop().event(), writer)
    writer.close()
    await writer.wait_closed()


async def test_synthesize_returns_valid_wav_with_expected_audio():
    server = await asyncio.start_server(_fake_piper_handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]

    async with server:
        serve_task = asyncio.ensure_future(server.serve_forever())
        try:
            client = PiperClient(f"tcp://127.0.0.1:{port}")
            wav_bytes = await client.synthesize("hello tau")
        finally:
            serve_task.cancel()

    assert wav_bytes.startswith(b"RIFF")
    assert FAKE_PCM in wav_bytes


async def _run_against(handler):
    """Boilerplate for the fake-Piper tests below: start a real TCP server on a free port, hand
    back a client pointed at it."""
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        serve_task = asyncio.ensure_future(server.serve_forever())
        try:
            yield PiperClient(f"tcp://127.0.0.1:{port}")
        finally:
            serve_task.cancel()


async def test_synthesize_puts_the_requested_voice_on_the_wire():
    """The compatibility pair to the test above: that one proves voice=None still sends a bare
    Synthesize, this one proves a named voice actually reaches Piper rather than being dropped."""
    seen: dict[str, object] = {}

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        event = await async_read_event(reader)
        assert event is not None and Synthesize.is_type(event.type)
        request = Synthesize.from_event(event)
        seen["voice"] = request.voice.name if request.voice else None
        await async_write_event(AudioStart(rate=16000, width=2, channels=1).event(), writer)
        await async_write_event(
            AudioChunk(audio=FAKE_PCM, rate=16000, width=2, channels=1).event(), writer
        )
        await async_write_event(AudioStop().event(), writer)
        writer.close()
        await writer.wait_closed()

    async for client in _run_against(handler):
        await client.synthesize("hello tau", voice="en_US-amy-medium")

    assert seen["voice"] == "en_US-amy-medium"


async def test_installed_voices_reports_only_what_piper_actually_has():
    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        event = await async_read_event(reader)
        assert event is not None and Describe.is_type(event.type)
        attribution = Attribution(name="rhasspy", url="https://github.com/rhasspy/piper")
        await async_write_event(
            Info(
                tts=[
                    TtsProgram(
                        name="piper",
                        attribution=attribution,
                        installed=True,
                        description=None,
                        version=None,
                        voices=[
                            TtsVoice(
                                name="en_US-lessac-medium", attribution=attribution, installed=True,
                                description=None, version=None, languages=["en_US"],
                            ),
                            TtsVoice(
                                name="en_US-amy-medium", attribution=attribution, installed=False,
                                description=None, version=None, languages=["en_US"],
                            ),
                        ],
                    )
                ]
            ).event(),
            writer,
        )
        writer.close()
        await writer.wait_closed()

    async for client in _run_against(handler):
        installed = await client.installed_voices()

    assert installed == {"en_US-lessac-medium"}


async def test_installed_voices_returns_none_when_piper_says_nothing():
    """None means "we don't know", which is different from an empty set meaning "Piper has
    nothing". The picker renders those two very differently - a probe failure must not grey out
    every voice."""

    async def handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.close()
        await writer.wait_closed()

    async for client in _run_against(handler):
        assert await client.installed_voices() is None
