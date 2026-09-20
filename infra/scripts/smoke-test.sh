#!/usr/bin/env bash
# Proves the Phase 0 exit criteria from project-tau-plan.md Section 3:
#   "you can spin up/tear down a k3s pod, pull a secret from Vault, and get a round-trip
#    response from Ollama - all before Tau's brain exists."
#
# Run this AFTER applying the full infra/ stack (see infra/README.md's apply order) and after
# infra/vault/scripts/seed-example-secret.sh has been run at least once.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
KUBECONFIG_PATH="${KUBECONFIG:-$REPO_ROOT/infra/k3s/kubeconfig}"
KEYS_FILE="$REPO_ROOT/infra/vault/scripts/vault-unseal-keys.json"
OLLAMA_HOST_URL="${OLLAMA_HOST_URL:-http://192.0.2.31:11434}" # override to your real Ollama VM IP

pass=true

echo "== 1/3: k3s pod spin-up/tear-down =="
if kubectl --kubeconfig "$KUBECONFIG_PATH" apply -f "$REPO_ROOT/infra/k3s/smoke-test/namespace.yaml" \
    -f "$REPO_ROOT/infra/k3s/smoke-test/smoke-pod.yaml" >/dev/null \
  && kubectl --kubeconfig "$KUBECONFIG_PATH" wait --for=condition=Ready pod/tau-smoke-pod \
    -n tau-smoke-test --timeout=60s >/dev/null \
  && kubectl --kubeconfig "$KUBECONFIG_PATH" delete -f "$REPO_ROOT/infra/k3s/smoke-test/smoke-pod.yaml" >/dev/null; then
  echo "PASS: k3s pod spun up and was torn down"
else
  echo "FAIL: k3s pod spin-up/tear-down"
  pass=false
fi

echo "== 2/3: Vault secret retrieval =="
if [[ -f "$KEYS_FILE" ]] && command -v jq >/dev/null 2>&1; then
  ROOT_TOKEN=$(jq -r ".root_token" "$KEYS_FILE")
  RESULT=$(kubectl --kubeconfig "$KUBECONFIG_PATH" -n vault exec -i vault-0 -- \
    env "VAULT_TOKEN=${ROOT_TOKEN}" vault kv get -field=note secret/tau-core/ollama 2>/dev/null)
  if [[ "$RESULT" == "smoke-test-value" ]]; then
    echo "PASS: retrieved secret/tau-core/ollama from Vault"
  else
    echo "FAIL: Vault secret retrieval (got '${RESULT:-<empty>}')"
    pass=false
  fi
else
  echo "FAIL: missing $KEYS_FILE or jq — run vault-init.sh/seed-example-secret.sh first"
  pass=false
fi

echo "== 3/3: Ollama round-trip =="
RESPONSE=$(curl -s -m 30 "${OLLAMA_HOST_URL}/api/generate" \
  -d '{"model":"qwen2.5:0.5b","prompt":"say ok","stream":false}' | jq -r ".response" 2>/dev/null)
if [[ -n "$RESPONSE" && "$RESPONSE" != "null" ]]; then
  echo "PASS: Ollama responded: ${RESPONSE:0:80}"
else
  echo "FAIL: Ollama round-trip (no response from ${OLLAMA_HOST_URL})"
  pass=false
fi

echo
if $pass; then
  echo "ALL PASS — Phase 0 exit criteria met: you can spin up/tear down a k3s pod, pull a secret from Vault, and get a round-trip response from Ollama."
  exit 0
else
  echo "One or more checks FAILED — Phase 0 exit criteria not yet met."
  exit 1
fi
