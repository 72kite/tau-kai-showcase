#!/usr/bin/env bash
# Initializes Vault (must be run exactly once, after the vault-0 pod is up and sealed).
# Writes unseal keys + root token to a LOCAL, GITIGNORED file. Store that file offline
# (password manager, printed and locked in a drawer, etc.) — never commit it, never leave
# it lying around on a shared machine.
set -euo pipefail

NAMESPACE="vault"
POD="vault-0"
OUT_FILE="$(dirname "$0")/vault-unseal-keys.json"

if [[ -f "$OUT_FILE" ]]; then
  echo "Refusing to overwrite existing $OUT_FILE (Vault appears already initialized)." >&2
  exit 1
fi

kubectl -n "$NAMESPACE" exec -i "$POD" -- \
  vault operator init -key-shares=5 -key-threshold=3 -format=json > "$OUT_FILE"

chmod 600 "$OUT_FILE"
echo "Wrote unseal keys + root token to $OUT_FILE. Move it somewhere safe, then run vault-unseal.sh."
