# Wikipedia ZIM size/coverage tiers

Confirmed live against `download.kiwix.org/zim/wikipedia/` (2026-08-10). Sizes shift slightly
between dated releases; treat these as representative, not exact.

| Variant | Size | Coverage | Notes |
|---|---|---|---|
| `wikipedia_en_top_nopic` | ~2.1GB | Top/most-notable articles only, text, no images | **Currently deployed.** Chosen for the storage-constrained deployment (see the Proxmox-side notes on deferred backups) - long-tail topics fall through to `research-mcp-server`. |
| `wikipedia_en-simple_all_maxi` | ~3.3GB | Every article, plain-language ("Simple English") rewrites, with images | Smaller vocabulary/less thorough than real English Wikipedia - a lookup, not a reference. |
| `wikipedia_en_all_mini` | ~12GB | Every article that exists, but each shortened (no references/extra detail) | Broadest *topic* coverage at a still-modest size. |
| `wikipedia_en_all_nopic` | ~49GB | Every article, full detail, no images | **The planned upgrade target** once the deployment's storage is upgraded (see project memory). Not deployed today - would consume most of the currently-free disk on the deployment CT. |
| `wikipedia_en_all_maxi` | ~115GB | Every article, full detail, with images | Not planned - no current need for embedded images given this is a text-only voice/chat assistant. |

Full, current listing: <https://download.kiwix.org/zim/wikipedia/>. Other subjects (medicine,
history, etc.) and other languages are available under the same directory structure if ever
needed.

## Switching tiers

```bash
ZIM_URL="https://download.kiwix.org/zim/wikipedia/wikipedia_en_all_nopic_<latest-date>.zim" \
  ./infra/kiwix/pull-wikipedia-zim.sh
docker compose up -d --force-recreate kiwix-serve
```

No other change needed - `KIWIX_ZIM_NAME` stays `wikipedia` regardless of which variant is
loaded, because `pull-wikipedia-zim.sh` always saves the downloaded file under that fixed name
(see its header comment, and `kiwix.py`'s module docstring, for why the on-disk filename and not
the ZIM's internal metadata is what matters here).
