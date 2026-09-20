# Initializes Vault (must be run exactly once, after the vault-0 pod is up and sealed).
# Writes unseal keys + root token to a LOCAL, GITIGNORED file. Store that file offline
# (password manager, printed and locked in a drawer, etc.) - never commit it, never leave
# it lying around on a shared machine.
$ErrorActionPreference = "Stop"

$Namespace = "vault"
$Pod = "vault-0"
$OutFile = Join-Path $PSScriptRoot "vault-unseal-keys.json"

if (Test-Path $OutFile) {
    Write-Error "Refusing to overwrite existing $OutFile (Vault appears already initialized)."
    exit 1
}

kubectl -n $Namespace exec -i $Pod -- vault operator init -key-shares=5 -key-threshold=3 -format=json |
    Out-File -FilePath $OutFile -Encoding utf8

Write-Host "Wrote unseal keys + root token to $OutFile. Move it somewhere safe, then run vault-unseal.ps1."
