# Read-only access to tau-core's own secrets. This is the concrete KV path a future
# VaultSecretsProvider (implementing tau_core.config.secrets.SecretsProvider) reads from —
# see tau-core/src/tau_core/config/secrets.py. Implementing that class is future Phase 1/2
# work, not part of this infra scaffold.
path "secret/data/tau-core/*" {
  capabilities = ["read"]
}

path "secret/metadata/tau-core/*" {
  capabilities = ["list", "read"]
}
