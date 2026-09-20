# voice-mcp-server needs two heavy, deliberately-opt-in pyproject.toml extras that the generic
# docker/mcp-server.Dockerfile's plain `pip install -e .` never installs: `speaker-id`
# (speechbrain + torch, for enroll_voiceprint/identify_speaker) and `wake-word` (openwakeword, for
# detect_wake_word). Its own Dockerfile, same as vision-mcp-server's, rather than a build-arg
# branch every other domain server sharing the generic file would also have to reason about.
FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir -e '.[speaker-id,wake-word]'

# Bundled openWakeWord models (hey_jarvis etc.) aren't fetched by Model() itself - see
# wake_word.py's _ensure_model(). Baking them in at build time means the container never needs a
# network call just to start detecting. A future custom "hey tau" .onnx would instead arrive via
# the voice-models volume + WAKEWORD_MODEL env var (see docker-compose.yml).
RUN python -c "import openwakeword; openwakeword.utils.download_models()"

ENV PYTHONUNBUFFERED=1 \
    FASTMCP_PORT=8000

CMD ["python", "-m", "voice_mcp_server.server"]
