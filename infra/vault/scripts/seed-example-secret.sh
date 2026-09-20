#!/usr/bin/env bash
# Writes one example secret so the smoke test (../../scripts/smoke-test.sh) has something real
# to read back via `vault kv get secret/tau-core/ollama`.
set -euo pipefail

NAMESPACE="vault"
POD="vault-0"
KEYS_FILE="$(dirname "$0")/vault-unseal-keys.json"

command -v jq >/dev/null 2>&1 || { echo "This script requires jq." >&2; exit 1; }
ROOT_TOKEN=$(jq -r ".root_token" "$KEYS_FILE")

kubectl -n "$NAMESPACE" exec -i "$POD" -- env "VAULT_TOKEN=${ROOT_TOKEN}" \
  vault kv put secret/tau-core/ollama endpoint="http://tau-ollama.default.svc.cluster.local:11434" note="smoke-test-value"

echo "Seeded secret/tau-core/ollama"
