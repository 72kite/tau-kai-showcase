import httpx

from voice_mcp_server.whisper_client import WhisperClient


def make_client(handler) -> WhisperClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(base_url="http://whisper.local", transport=transport)
    return WhisperClient("http://whisper.local", http_client=http_client)


async def test_transcribe_posts_multipart_file_and_returns_text():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/audio/transcriptions"
        assert request.method == "POST"
        assert b'name="file"; filename="clip.wav"' in request.content
        assert b"fake-wav-bytes" in request.content
        return httpx.Response(200, json={"text": "turn on the kitchen light"})

    client = make_client(handler)
    text = await client.transcribe(b"fake-wav-bytes", filename="clip.wav")

    assert text == "turn on the kitchen light"
