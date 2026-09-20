# Enables Vault's Kubernetes auth method and creates the tau-core role/policy, so pods
# authenticate via their ServiceAccount token instead of a static Vault token embedded anywhere.
# Run after vault-init.ps1 + vault-unseal.ps1.
$ErrorActionPreference = "Stop"

$Namespace = "vault"
$Pod = "vault-0"
$KeysFile = Join-Path $PSScriptRoot "vault-unseal-keys.json"
$PolicyDir = Join-Path $PSScriptRoot "..\policies"

$RootToken = (Get-Content $KeysFile -Raw | ConvertFrom-Json).root_token

function Invoke-Vault {
    param([Parameter(ValueFromRemainingArguments)]$Args)
    kubectl -n $Namespace exec -i $Pod -- env "VAULT_TOKEN=$RootToken" @Args
}

try { Invoke-Vault vault secrets enable -path=secret kv-v2 } catch { Write-Host "secret/ KV engine already enabled" }
try { Invoke-Vault vault auth enable kubernetes } catch { Write-Host "kubernetes auth already enabled" }

$KubeCaCert = kubectl -n $Namespace exec -i $Pod -- cat /var/run/secrets/kubernetes.io/serviceaccount/ca.crt
$TokenReviewerJwt = kubectl -n $Namespace exec -i $Pod -- cat /var/run/secrets/kubernetes.io/serviceaccount/token

Invoke-Vault vault write auth/kubernetes/config `
    kubernetes_host="https://kubernetes.default.svc:443" `
    kubernetes_ca_cert="$KubeCaCert" `
    token_reviewer_jwt="$TokenReviewerJwt"

kubectl -n $Namespace cp "$PolicyDir\tau-core-policy.hcl" "${Pod}:/tmp/tau-core-policy.hcl"
Invoke-Vault vault policy write tau-core /tmp/tau-core-policy.hcl

Invoke-Vault vault write auth/kubernetes/role/tau-core `
    bound_service_account_names=tau-core `
    bound_service_account_namespaces=default `
    policies=tau-core `
    ttl=1h

Invoke-Vault vault write auth/kubernetes/role/tau-smoke-test `
    bound_service_account_names=default `
    bound_service_account_namespaces=tau-smoke-test `
    policies=tau-core `
    ttl=1h

Write-Host "Kubernetes auth method configured; tau-core and tau-smoke-test roles created."
