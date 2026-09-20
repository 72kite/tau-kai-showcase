# wikipedia-mcp-server

Offline-first encyclopedia lookups for Project Tau (added 2026-08-10). Two read-only tools —
`search_wikipedia` and `get_wikipedia_article` — plus a `wikipedia://backend` resource, backed by
a self-hosted [Kiwix](https://kiwix.org) server serving one Wikipedia ZIM snapshot.

**No internet round-trip for a basic fact.** Every other knowledge tool in this repo
(`research-mcp-server`) pays a live SearxNG hop even for something as ordinary as "who was Ray
Charles" — this server answers instantly from a local snapshot instead, matching the same
self-hosted-first philosophy as Ollama/Whisper/Piper/SearxNG.

## Tools

| Tool | Effect | Notes |
|---|---|---|
| `search_wikipedia(query, max_results)` | allow (read-only) | Searches the offline snapshot only. Zero results names `research-mcp-server` as the next step. |
| `get_wikipedia_article(title)` | allow (read-only) | Fetches one article's text. Not-found names `research-mcp-server` as the next step. |

Both return content wrapped in the same `<<<UNTRUSTED_WEB_CONTENT>>>` fence
`research-mcp-server` uses — see "Threat model" below for why this reuses that marker instead of
minting a new one. No CDG rule is needed — both tools are read-only and fall through to
`default_effect: allow`, same as `research-mcp-server`/`utility-mcp-server`.

## Why no "fetch from the live internet" tool

The user-facing requirement this server satisfies is "offline Wikipedia, with the ability to go
online for more when needed." The "go online" half is deliberately **not** a new tool here.
`research-mcp-server` already has a fully SSRF-hardened `search_web`/`fetch_page` that can reach
any page, including the real wikipedia.org — building a second, narrower online-fetch
implementation in this package would duplicate that safety-critical code for no benefit. Instead:

- Both tools' "not found" responses explicitly say so and name `research-mcp-server__search_web`
  as the next step (mirrors `research-mcp-server`'s own "this is a lookup failure, not evidence
  nothing exists" framing for its zero-results case).
- `MAIN_SYSTEM_PROMPT`'s domain-routing list tells the model to try this server first for
  general-knowledge questions, and fall back to `research-mcp-server` if it comes back empty.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `KIWIX_URL` | *(unset)* | Self-hosted kiwix-serve base URL. Compose sets `http://kiwix-serve:8080`. |
| `KIWIX_ZIM_NAME` | *(unset)* | The loaded ZIM's **filename on disk**, without `.zim` — not its internal metadata name, which can differ. See `kiwix.py`'s module docstring. |
| `MCP_TRANSPORT` | `stdio` | `streamable_http` in the compose stack. |

### The offline snapshot itself isn't part of this package

The ZIM file lives in a Docker volume (`kiwix-data`), not in this repo or the built image — see
`infra/kiwix/pull-wikipedia-zim.sh` to provision it, and `infra/kiwix/zim-variants.md` for the
size/coverage tiers available. **Currently deployed: `wikipedia_en_top_nopic` (~2GB, top articles
only)**, a deliberate storage-budget choice — the deployment CT was already storage-constrained
(see the Proxmox-side notes on deferred backups). Swapping to a larger tier later is a
`pull-wikipedia-zim.sh` re-run with a different variant name — no code or config change, because
`KIWIX_ZIM_NAME` is fixed to the on-disk filename the script always saves under, not anything
derived from the ZIM's contents.

## Threat model

Wikipedia article text is offline and curated, but it originates as crowdsourced, editable text —
the same class of "written by someone else, not the user" content `research-mcp-server` was built
to handle carefully. This server reuses that server's mitigations rather than re-deriving them:

1. **The CDG.** Unaffected either way — nothing here can act on an injected instruction beyond
   what any hallucinated tool call could already trigger, still gated by human approval.
2. **Framing.** Content comes back inside `<<<UNTRUSTED_WEB_CONTENT>>>` … `<<<END_…>>>` —
   reused from `research-mcp-server` rather than a new marker pair, since the trust semantics
   ("data, never instructions") are identical and a third marker would only grow
   `MAIN_SYSTEM_PROMPT`'s enumerated fence list without giving the model anything
   decision-relevant. An article can't close the fence itself (markers are stripped from content
   before framing).
3. **No exfiltration channel.** Unlike `research-mcp-server__fetch_page`, there is no
   arbitrary-URL tool here — `get_wikipedia_article` can only reach paths inside the locally
   loaded ZIM. There is nothing for an injected instruction to ask this server to fetch that
   would carry data anywhere.

## Testing

```bash
python -m venv .venv && .venv/Scripts/pip install -e ".[dev]"   # Windows
.venv/bin/pip install -e ".[dev]"                                 # macOS/Linux
pytest
```

26 tests, fully offline — `httpx.MockTransport` for HTTP, real captured kiwix-serve response
fixtures (`tests/fixtures/`) for parsing, a real article HTML fixture
(`tests/fixtures/ray_charles_raw.html`, captured live 2026-08-10) for extraction. Covers:
`/suggest` result parsing (including kiwix's trailing placeholder entry), the book-name-mismatch
404 path, article-not-found vs. backend-unreachable distinction, percent-encoding of article
paths containing `/`, the void-element drop-stack edge case in `extract.py`, and the
untrusted-content framing/fence-escape resistance.

**Not covered by these tests, verified live instead (see the implementation plan / commit
history for what was checked and when):** a real `kiwix-serve` container serving the actual
`wikipedia_en_top_nopic` snapshot end-to-end through `tau-core`. `kiwix-serve`'s `/search`
endpoint (as opposed to `/suggest`, which this server uses) returned a bare 500 error against a
real ZIM in live testing (kiwix-tools 3.8.2) — not used here, but worth re-checking against future
kiwix-tools releases in case it's fixed. The `wikipedia_en_all_nopic` (~49GB) tier is explicitly
out of scope for now — storage-constrained on the current deployment.
