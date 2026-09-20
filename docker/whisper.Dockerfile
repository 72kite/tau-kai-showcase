# Local faster-whisper STT service (infra/whisper/local_server.py) as a compose service, so the
# whole voice pipeline runs inside Docker instead of needing a separately-launched host process.
#
# CPU by default (python:3.11-slim + int8 compute). For GPU, docker-compose.gpu.yml rebuilds this
# with BASE_IMAGE set to an NVIDIA CUDA+cuDNN runtime image (CTranslate2, faster-whisper's backend,
# loads cuDNN at runtime) and flips WHISPER_DEVICE=cuda. On the CUDA base the slim image's
# preinstalled python is absent, so we install it explicitly - the ARG guards keep the CPU path
# unchanged.
ARG BASE_IMAGE=python:3.11-slim
FROM ${BASE_IMAGE}

# Present only on the CUDA base (empty string on python:3.11-slim, so the RUN is a no-op there).
ARG INSTALL_PYTHON=""
RUN if [ -n "${INSTALL_PYTHON}" ]; then \
        apt-get update && apt-get install -y --no-install-recommends \
            python3 python3-pip python3-venv && \
        ln -sf /usr/bin/python3 /usr/local/bin/python && \
        ln -sf /usr/bin/pip3 /usr/local/bin/pip && \
        rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY local_server.py ./

ENV PYTHONUNBUFFERED=1 \
    WHISPER_PORT=9000 \
    WHISPER_MODEL=base \
    WHISPER_DEVICE=cpu

EXPOSE 9000
CMD ["python", "local_server.py"]
