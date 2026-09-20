"""Local, fully-offline speech-to-text service for Project Tau.

Serves the same OpenAI-compatible endpoint voice-mcp-server's WhisperClient already targets
(POST /v1/audio/transcriptions, multipart file -> {"text": ...}), backed by faster-whisper
(MIT-licensed CTranslate2 reimplementation of OpenAI Whisper). No audio ever leaves the
machine - this replaces the browser SpeechRecognition path's dependency on Google/Azure cloud
recognizers (see frontend/README.md's constraints section).

Run (from infra/whisper/, its own venv):
    pip install -r requirements.txt
    python local_server.py            # binds 0.0.0.0:9000

Env:
    WHISPER_MODEL   faster-whisper model name/size (default "base": ~74MB download on first
                    run, cached under ~/.cache/huggingface). "small" is noticeably more
                    accurate for ~2x the CPU time; "tiny" if the host is very weak.
    WHISPER_DEVICE  "cpu" (default) or "cuda"
    WHISPER_PORT    default 9000

Point voice-mcp-server at it with WHISPER_URL=http://localhost:9000 (tau-core/.env).
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI, File, Form, UploadFile

app = FastAPI(title="Tau local Whisper")

_model = None


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel

        name = os.environ.get("WHISPER_MODEL", "base")
        device = os.environ.get("WHISPER_DEVICE", "cpu")
        # int8 halves memory and roughly doubles CPU throughput for a negligible accuracy cost
        # at these model sizes. On GPU, float16 is the usual pick (set WHISPER_COMPUTE_TYPE=
        # float16 via docker-compose.gpu.yml); default keeps the CPU path unchanged.
        compute_type = os.environ.get(
            "WHISPER_COMPUTE_TYPE", "float16" if device == "cuda" else "int8"
        )
        _model = WhisperModel(name, device=device, compute_type=compute_type)
    return _model


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": os.environ.get("WHISPER_MODEL", "base")}


@app.post("/v1/audio/transcriptions")
async def transcribe(file: UploadFile = File(...), model: str = Form("whisper")) -> dict:
    """OpenAI-compatible transcription: accepts whatever container the browser's MediaRecorder
    produced (webm/opus, mp4/aac, wav) - faster-whisper decodes via PyAV, no ffmpeg CLI needed.
    The `model` form field is accepted for API compatibility and ignored; the loaded model is
    chosen by WHISPER_MODEL at startup.
    """
    import io

    audio_bytes = await file.read()
    segments, _info = _get_model().transcribe(io.BytesIO(audio_bytes), vad_filter=True)
    text = " ".join(segment.text.strip() for segment in segments).strip()
    return {"text": text}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("WHISPER_PORT", "9000")))
