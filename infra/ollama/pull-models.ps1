# Pulls the default models (see models.md) into the Ollama instance at $env:OLLAMA_HOST
# (defaults to localhost, i.e. run this ON the Ollama VM; or set OLLAMA_HOST to run remotely).
$ErrorActionPreference = "Stop"

if (-not $env:OLLAMA_HOST) { $env:OLLAMA_HOST = "http://127.0.0.1:11434" }

$Models = @("llama3.1:8b", "qwen2.5:0.5b")

foreach ($model in $Models) {
    Write-Host "Pulling $model ..."
    ollama pull $model
}

Write-Host "Done. Installed models:"
ollama list
