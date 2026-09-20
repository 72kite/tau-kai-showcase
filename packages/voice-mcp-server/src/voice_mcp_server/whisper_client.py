from __future__ import annotations

import httpx


class WhisperClient:
    """Thin async wrapper around faster-whisper-server's OpenAI-compatible transcription API
    (infra/k3s/voice/whisper-deployment.yaml). Has no knowledge of MCP or env vars.
    """

    def __init__(self, base_url: str, http_client: httpx.AsyncClient | None = None):
        self._client = http_client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=30.0)

    async def transcribe(self, audio: bytes, filename: str = "audio.wav") -> str:
        files = {"file": (filename, audio, "audio/wav")}
        response = await self._client.post("/v1/audio/transcriptions", files=files, data={"model": "whisper"})
        response.raise_for_status()
        return response.json()["text"]

    async def aclose(self) -> None:
        await self._client.aclose()
