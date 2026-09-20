#!/usr/bin/env bash
# Unseals Vault using 3 of the 5 keys from vault-init.sh's output. Manual unseal is accepted at
# home-lab scale (see infra/README.md tool-choice table) rather than standing up auto-unseal
# infrastructure (cloud KMS / another Vault / etc.).
set -euo pipefail

NAMESPACE="vault"
POD="vault-0"
KEYS_FILE="$(dirname "$0")/vault-unseal-keys.json"

if [[ ! -f "$KEYS_FILE" ]]; then
  echo "Missing $KEYS_FILE. Run vault-init.sh first (once)." >&2
  exit 1
fi

command -v jq >/dev/null 2>&1 || { echo "This script requires jq." >&2; exit 1; }

for i in 0 1 2; do
  KEY=$(jq -r ".unseal_keys_b64[$i]" "$KEYS_FILE")
  kubectl -n "$NAMESPACE" exec -i "$POD" -- vault operator unseal "$KEY" >/dev/null
done

echo "Vault unsealed."
