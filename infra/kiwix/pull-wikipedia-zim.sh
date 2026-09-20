#!/usr/bin/env bash
# Downloads a Kiwix Wikipedia ZIM into the `kiwix-data` Docker volume, always saving it as
# wikipedia.zim regardless of which variant/date was fetched. wikipedia-mcp-server's
# KIWIX_ZIM_NAME points at that fixed filename, not the ZIM's own internal metadata name (which
# can differ - see packages/wikipedia-mcp-server/src/wikipedia_mcp_server/kiwix.py's module
# docstring for how that was confirmed live), so swapping tiers later never needs a code/config
# change - just re-run this with a different ZIM_URL.
#
# Usage:
#   ./pull-wikipedia-zim.sh                    # default: top articles, text-only (~2GB)
#   ZIM_URL=<...> ./pull-wikipedia-zim.sh       # any other tier - see zim-variants.md
#
# Run once after `docker compose up kiwix-serve` (or before - the named volume is created either
# way by this script's own `docker run -v`). Downloads via a one-off curl container rather than
# `docker exec`-ing into kiwix-serve itself: that image's internals aren't ours to assume a shell
# or curl exist in (same reasoning as this repo's other third-party-image services).
#
# `-u 0`: a freshly created named volume is root-owned, and curlimages/curl's default image user
# is non-root - confirmed live (2026-08-10): without this, curl silently gets "Permission denied"
# writing the first byte into the empty volume.
set -euo pipefail

DEFAULT_ZIM_URL="https://download.kiwix.org/zim/wikipedia/wikipedia_en_top_nopic_2026-06.zim"
ZIM_URL="${ZIM_URL:-$DEFAULT_ZIM_URL}"
VOLUME="${KIWIX_VOLUME:-tau-kai_kiwix-data}"

echo "Downloading:"
echo "  $ZIM_URL"
echo "into volume '$VOLUME' as wikipedia.zim (see zim-variants.md for size/coverage tiers) ..."

docker run --rm \
  -u 0 \
  -v "${VOLUME}:/data" \
  curlimages/curl:latest \
  -L --fail --show-error -o /data/wikipedia.zim "$ZIM_URL"

echo "Done."
echo "If kiwix-serve is already running, restart it to pick up the new file:"
echo "  docker compose up -d --force-recreate kiwix-serve"
