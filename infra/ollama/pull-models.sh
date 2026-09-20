#!/usr/bin/env bash
# Pulls the default models (see models.md) into the Ollama instance at $OLLAMA_HOST
# (defaults to localhost, i.e. run this ON the Ollama VM; or export OLLAMA_HOST to run remotely).
set -euo pipefail

export OLLAMA_HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"

MODELS=(
  "llama3.1:8b"
  "qwen2.5:0.5b"
)

for model in "${MODELS[@]}"; do
  echo "Pulling $model ..."
  ollama pull "$model"
done

echo "Done. Installed models:"
ollama list
