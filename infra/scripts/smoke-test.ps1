# Proves the Phase 0 exit criteria from project-tau-plan.md Section 3:
#   "you can spin up/tear down a k3s pod, pull a secret from Vault, and get a round-trip
#    response from Ollama - all before Tau's brain exists."
#
# Run this AFTER applying the full infra/ stack (see infra/README.md's apply order) and after
# infra/vault/scripts/seed-example-secret.ps1 has been run at least once.

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$KubeconfigPath = if ($env:KUBECONFIG) { $env:KUBECONFIG } else { Join-Path $RepoRoot "infra\k3s\kubeconfig" }
$KeysFile = Join-Path $RepoRoot "infra\vault\scripts\vault-unseal-keys.json"
$OllamaHostUrl = if ($env:OLLAMA_HOST_URL) { $env:OLLAMA_HOST_URL } else { "http://192.0.2.31:11434" }

$pass = $true

Write-Host "== 1/3: k3s pod spin-up/tear-down =="
try {
    kubectl --kubeconfig $KubeconfigPath apply `
        -f "$RepoRoot\infra\k3s\smoke-test\namespace.yaml" `
        -f "$RepoRoot\infra\k3s\smoke-test\smoke-pod.yaml" | Out-Null
    kubectl --kubeconfig $KubeconfigPath wait --for=condition=Ready pod/tau-smoke-pod `
        -n tau-smoke-test --timeout=60s | Out-Null
    kubectl --kubeconfig $KubeconfigPath delete -f "$RepoRoot\infra\k3s\smoke-test\smoke-pod.yaml" | Out-Null
    Write-Host "PASS: k3s pod spun up and was torn down"
} catch {
    Write-Host "FAIL: k3s pod spin-up/tear-down"
    $pass = $false
}

Write-Host "== 2/3: Vault secret retrieval =="
if (Test-Path $KeysFile) {
    $RootToken = (Get-Content $KeysFile -Raw | ConvertFrom-Json).root_token
    $result = kubectl --kubeconfig $KubeconfigPath -n vault exec -i vault-0 -- `
        env "VAULT_TOKEN=$RootToken" vault kv get -field=note secret/tau-core/ollama 2>$null
    if ($result -eq "smoke-test-value") {
        Write-Host "PASS: retrieved secret/tau-core/ollama from Vault"
    } else {
        Write-Host "FAIL: Vault secret retrieval (got '$result')"
        $pass = $false
    }
} else {
    Write-Host "FAIL: missing $KeysFile - run vault-init.ps1/seed-example-secret.ps1 first"
    $pass = $false
}

Write-Host "== 3/3: Ollama round-trip =="
try {
    $body = '{"model":"qwen2.5:0.5b","prompt":"say ok","stream":false}'
    $resp = Invoke-RestMethod -Uri "$OllamaHostUrl/api/generate" -Method Post -Body $body -ContentType "application/json" -TimeoutSec 30
    if ($resp.response) {
        Write-Host "PASS: Ollama responded: $($resp.response.Substring(0, [Math]::Min(80, $resp.response.Length)))"
    } else {
        Write-Host "FAIL: Ollama round-trip (empty response)"
        $pass = $false
    }
} catch {
    Write-Host "FAIL: Ollama round-trip (no response from $OllamaHostUrl)"
    $pass = $false
}

Write-Host ""
if ($pass) {
    Write-Host "ALL PASS - Phase 0 exit criteria met: you can spin up/tear down a k3s pod, pull a secret from Vault, and get a round-trip response from Ollama."
    exit 0
} else {
    Write-Host "One or more checks FAILED - Phase 0 exit criteria not yet met."
    exit 1
}
