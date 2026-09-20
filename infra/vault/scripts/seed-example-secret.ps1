# Writes one example secret so the smoke test (..\..\scripts\smoke-test.ps1) has something real
# to read back via `vault kv get secret/tau-core/ollama`.
$ErrorActionPreference = "Stop"

$Namespace = "vault"
$Pod = "vault-0"
$KeysFile = Join-Path $PSScriptRoot "vault-unseal-keys.json"

$RootToken = (Get-Content $KeysFile -Raw | ConvertFrom-Json).root_token

kubectl -n $Namespace exec -i $Pod -- env "VAULT_TOKEN=$RootToken" `
    vault kv put secret/tau-core/ollama endpoint="http://tau-ollama.default.svc.cluster.local:11434" note="smoke-test-value"

Write-Host "Seeded secret/tau-core/ollama"
