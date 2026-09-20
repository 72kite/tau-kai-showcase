# Downloads a Kiwix Wikipedia ZIM into the `kiwix-data` Docker volume, always saving it as
# wikipedia.zim regardless of which variant/date was fetched. wikipedia-mcp-server's
# KIWIX_ZIM_NAME points at that fixed filename, not the ZIM's own internal metadata name (which
# can differ - see packages/wikipedia-mcp-server/src/wikipedia_mcp_server/kiwix.py's module
# docstring), so swapping tiers later never needs a code/config change - just re-run this with a
# different $env:ZIM_URL. See zim-variants.md for the available tiers.
$ErrorActionPreference = "Stop"

if (-not $env:ZIM_URL) {
    $env:ZIM_URL = "https://download.kiwix.org/zim/wikipedia/wikipedia_en_top_nopic_2026-06.zim"
}
if (-not $env:KIWIX_VOLUME) {
    $env:KIWIX_VOLUME = "tau-kai_kiwix-data"
}

Write-Host "Downloading:"
Write-Host "  $env:ZIM_URL"
Write-Host "into volume '$env:KIWIX_VOLUME' as wikipedia.zim (see zim-variants.md for size/coverage tiers) ..."

docker run --rm `
  -u 0 `
  -v "${env:KIWIX_VOLUME}:/data" `
  curlimages/curl:latest `
  -L --fail --show-error -o /data/wikipedia.zim "$env:ZIM_URL"

Write-Host "Done."
Write-Host "If kiwix-serve is already running, restart it to pick up the new file:"
Write-Host "  docker compose up -d --force-recreate kiwix-serve"
