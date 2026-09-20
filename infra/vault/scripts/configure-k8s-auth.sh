#!/usr/bin/env bash
# Enables Vault's Kubernetes auth method and creates the tau-core role/policy, so pods
# authenticate via their ServiceAccount token instead of a static Vault token embedded anywhere.
# Run after vault-init.sh + vault-unseal.sh.
set -euo pipefail

NAMESPACE="vault"
POD="vault-0"
KEYS_FILE="$(dirname "$0")/vault-unseal-keys.json"
POLICY_DIR="$(dirname "$0")/../policies"

command -v jq >/dev/null 2>&1 || { echo "This script requires jq." >&2; exit 1; }
ROOT_TOKEN=$(jq -r ".root_token" "$KEYS_FILE")

vault_exec() {
  kubectl -n "$NAMESPACE" exec -i "$POD" -- env "VAULT_TOKEN=${ROOT_TOKEN}" "$@"
}

vault_exec vault secrets enable -path=secret kv-v2 2>/dev/null || echo "secret/ KV engine already enabled"
vault_exec vault auth enable kubernetes 2>/dev/null || echo "kubernetes auth already enabled"

KUBE_CA_CERT=$(kubectl -n "$NAMESPACE" exec -i "$POD" -- cat /var/run/secrets/kubernetes.io/serviceaccount/ca.crt)
TOKEN_REVIEWER_JWT=$(kubectl -n "$NAMESPACE" exec -i "$POD" -- cat /var/run/secrets/kubernetes.io/serviceaccount/token)

vault_exec vault write auth/kubernetes/config \
  kubernetes_host="https://kubernetes.default.svc:443" \
  kubernetes_ca_cert="${KUBE_CA_CERT}" \
  token_reviewer_jwt="${TOKEN_REVIEWER_JWT}"

kubectl -n "$NAMESPACE" cp "${POLICY_DIR}/tau-core-policy.hcl" "${POD}:/tmp/tau-core-policy.hcl"
vault_exec vault policy write tau-core /tmp/tau-core-policy.hcl

vault_exec vault write auth/kubernetes/role/tau-core \
  bound_service_account_names=tau-core \
  bound_service_account_namespaces=default \
  policies=tau-core \
  ttl=1h

vault_exec vault write auth/kubernetes/role/tau-smoke-test \
  bound_service_account_names=default \
  bound_service_account_namespaces=tau-smoke-test \
  policies=tau-core \
  ttl=1h

echo "Kubernetes auth method configured; tau-core and tau-smoke-test roles created."
