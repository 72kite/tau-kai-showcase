# Unseals Vault using 3 of the 5 keys from vault-init.ps1's output. Manual unseal is accepted at
# home-lab scale (see infra/README.md tool-choice table) rather than standing up auto-unseal
# infrastructure (cloud KMS / another Vault / etc.).
$ErrorActionPreference = "Stop"

$Namespace = "vault"
$Pod = "vault-0"
$KeysFile = Join-Path $PSScriptRoot "vault-unseal-keys.json"

if (-not (Test-Path $KeysFile)) {
    Write-Error "Missing $KeysFile. Run vault-init.ps1 first (once)."
    exit 1
}

$keys = (Get-Content $KeysFile -Raw | ConvertFrom-Json).unseal_keys_b64

foreach ($key in $keys[0..2]) {
    kubectl -n $Namespace exec -i $Pod -- vault operator unseal $key | Out-Null
}

Write-Host "Vault unsealed."
