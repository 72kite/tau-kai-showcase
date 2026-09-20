# Local Whisper STT (faster-whisper)

Fully-offline speech-to-text for Project Tau: an OpenAI-compatible
`POST /v1/audio/transcriptions` endpoint backed by [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(MIT-licensed CTranslate2 port of OpenAI Whisper). This is the service
`voice-mcp-server`'s `transcribe` tool targets via `WHISPER_URL`, and what the frontend's
`VITE_VOICE_MODE=local` capture path depends on end to end. No audio leaves the machine —
this replaces the browser SpeechRecognition path's Google/Azure cloud dependency.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python local_server.py     # binds 0.0.0.0:9000
```

Then in `tau-core/.env`: `WHISPER_URL=http://localhost:9000`, and in `frontend/.env`:
`VITE_VOICE_MODE=local`.

Env knobs: `WHISPER_MODEL` (default `base`, ~74MB, downloaded+cached on first transcription;
`small` is more accurate at ~2x CPU; `tiny` for weak hosts), `WHISPER_DEVICE` (`cpu`/`cuda`),
`WHISPER_PORT` (9000).

Accepts whatever the browser's MediaRecorder produces (webm/opus, mp4/aac, wav) — decoding is
in-process via PyAV, no ffmpeg CLI needed. Verified end to end 2026-07-11 with a synthesized
WAV through the whole chain (bridge → CDG-audited voice-mcp-server call → this service).

The k3s deployment for the same contract is `infra/k3s/voice/whisper-deployment.yaml` (Phase 0);
this local server exists so voice works before any cluster does.
