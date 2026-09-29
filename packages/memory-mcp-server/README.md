# memory-mcp-server

The memory MCP server for Project Tau — see `project-tau-plan.md`
Section 5.4. Phase 2's third domain server: stores face/voice embeddings and per-person profiles
(access levels) for recognition. Independently deployable, like every domain server in Tau's
architecture — this package does not depend on `tau-core`.

## Scope: this server does not compute embeddings

Face/voice embedding models (`insightface`/`speechbrain` per the plan's OSS shortlist) are heavy,
model-downloading dependencies that belong to the servers that own the raw sensor pipelines -
`vision-mcp-server` for camera frames, `voice-mcp-server` for audio. This server only stores and
searches already-computed `embedding: list[float]` vectors. It never processes an image or audio
clip itself.

## Layout

```
src/memory_mcp_server/
  embedding_store.py   EmbeddingStore - two Chroma collections ("faces", "voices"):
                       store(kind, person_id, embedding), match(kind, embedding, top_k=1)
  profile_store.py      ProfileStore - JSON-file-backed person_id -> {access_level, ...}
  memory_tree.py         MemoryTreeStore - Phase 2.8: SQLite-indexed, Markdown-backed
                         hierarchical project/context memory (see its own section below)
  server.py              FastMCP tools wiring all three together
tests/
  test_embedding_store.py   chromadb.EphemeralClient() - fully in-memory, no disk/network
  test_profile_store.py     tmp_path-backed JSON file
  test_memory_tree.py       tmp_path-backed SQLite + vault directory
  test_server.py             tool functions called directly, injected fake stores
```

## Tools

- `store_face(person_id, embedding)` / `store_voice(person_id, embedding)` — store a new
  embedding sample and ensure the person has a profile (defaults to `access_level: "unknown"`).
  **Requires human approval** — enrolling a new recognized person is security-relevant, gated by
  `tau-core/config/cdg_rules.yaml`'s `memory-face-enrollment-needs-approval` /
  `memory-voice-enrollment-needs-approval` rules.
- `match_face(embedding, top_k=1)` / `match_voice(embedding, top_k=1)` — read-only similarity
  search, returns `[{"person_id": ..., "distance": ...}, ...]`.
- `get_person_profile(person_id)` — read-only profile lookup.
- `list_people()` — read-only, returns `{"people": [{person_id, access_level, face_count,
  voice_count}, ...]}` for every known person (union of profiles and stored embeddings, sorted
  by `person_id`). Wrapped in a dict rather than returned as a bare list — see the docstring in
  `server.py` for why (FastMCP serializes each list item as its own content block, so a caller
  reading only the first block would otherwise see just one person).
- `set_access_level(person_id, access_level)` — changes a person's access level. **Requires human
  approval** (`memory-access-level-change-needs-approval` rule) - privilege changes shouldn't be
  silent.
- `set_person_portrait(person_id, svg="", ascii_art="")` — caches an e-ink-style portrait
  drawing on that person's profile (`portrait_svg`/`portrait_ascii` fields), so
  `ui-bridge-mcp-server`'s recognition card doesn't need Tau to redraw it on every future
  recognition. Cosmetic metadata, not biometric data or an access change - no approval required.
- `store_memory(title, content, parent_id="", tags="")` — create a new Memory Tree node.
  **Requires human approval** (`memory-tree-store-needs-approval` rule), same posture as
  face/voice enrollment.
- `search_memory(query, limit=10, owner="")` — read-only keyword search over Memory Tree nodes,
  scoped to one speaker (see "Per-speaker memory" below).
- `get_memory_tree(root_id="")` — read-only, returns the tree (or a subtree) as nested JSON.
- `reinforce_memory(node_id)` — bump a node's score. Read-write but safe (only adjusts an
  existing node's score, creates/deletes nothing), so no approval required.

## Per-speaker memory (Phase 13.5)

Every Memory Tree node carries an `owner` — the voice-identified speaker it belongs to (`""` =
unattributed/household-shared). `search_memory(owner=X)` returns only that speaker's nodes plus
shared ones (`owner=""`), never another person's — for unverified **drafts** and human-approved
memories alike ("everything per-speaker"), so one person's inferences don't surface on another's
tablet. Promotion preserves the owner (approving a person's draft keeps it theirs). Legacy nodes
predating this field read as shared (an additive migration backfills `owner=""`), and a mis- or
un-identified speaker also falls to shared — the safe direction, never a false attribution.

`owner` is set **by tau-core, server-side**, from the same voice ID that tags the transcript — the
model calling these tools never gets to choose it (that would let one turn read or write another
person's memories). `list_drafts` stays **unscoped** (it's the admin review queue across all
speakers) but includes `owner` in each record so a reviewer sees whose inference a draft is.

## Two separate stores, joined only by `person_id`

A person can have several face/voice samples but only one access level, so profile fields aren't
duplicated onto every embedding. `EmbeddingStore` never knows about access levels;
`ProfileStore` never knows about vectors.

## Memory Tree Engine (Phase 2.8, OpenHuman-derived)

Face/voice data is genuinely embedding-shaped (fixed-length vectors, similarity search is the
right tool) and stays on Chroma via `EmbeddingStore`, unchanged. Project and conversational
context memory is a different shape of problem: an [OpenHuman](https://github.com/tinyhumansai/openhuman)
audit found that for this kind of memory, plain interpretable Markdown beats vector embeddings
- a human (or Tau) can read, edit, and directly reason about a memory node's content, which an
embedding can't offer, and it can double as a real Obsidian vault instead of a black box only
queryable through MCP tools.

`MemoryTreeStore` (`memory_tree.py`) is a from-scratch Python rewrite of that idea (not a
vendored copy of OpenHuman's Rust code — see the naming note below), built on two things:

- **SQLite** (`MEMORY_TREE_DB_PATH`, default `./data/memory_tree.sqlite`) holds the structural
  index: parent/child links, score, tags, timestamps. This is what makes search and tree
  traversal fast without re-parsing every Markdown file on every call.
- **Markdown files** (`MEMORY_VAULT_PATH`, default `./data/memory_vault`) hold the actual
  content, one `.md` file per node with a YAML frontmatter block (`id`, `title`, `tags`). Point
  Obsidian (or any Markdown-aware tool) at this directory and the memory tree is directly
  browsable and editable outside of Tau entirely.

Search is case-insensitive term matching over title/tags/content: a node matches if any query
term appears in it, ranked by distinct terms matched, then score, then recency (upgraded
2026-07-11 from whole-string substring matching so conversational recall queries like "what did
we decide about the kitchen lights" can match a node titled "Kitchen lighting decision").
Deliberately still not embedding similarity, for the same interpretability reason the whole
engine exists — "matched 2 of 3 terms" is an explanation a human can verify by reading the node. `reinforce_memory` is the scoring mechanism: each call adds a fixed delta
(default `+0.5`) to a node's score, so frequently-useful memories naturally rise to the top of
`search_memory`/`top_scored` results over time, and a human can see exactly why (a plain number
going up), not infer it from a similarity threshold.

**Naming note:** the original integration notes called this "Phase 2.5," matching OpenHuman's
own roadmap language. That collided with this repo's existing convention, where `2.5` already
names `vision-mcp-server` (the fifth domain server in Phase 2's build order — see
`project-tau-plan.md` section 5.5). It's renumbered to **Phase 2.8** here to avoid two things
meaning the same digits.

## Setup

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # defaults are fine for local dev; never commit .env
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `memory-mcp-server`, spawned via
`python -m memory_mcp_server.server`. Needs this package installed
(`pip install -e ../memory-mcp-server`) into whatever environment `tau-core` runs
`command: python` with — simplest for local dev is tau-core's own `.venv`.

`MEMORY_DB_PATH`/`MEMORY_PROFILES_PATH` default to `./data/chroma` and `./data/profiles.json` if
unset, so the server works out of the box for local dev without any required env vars (unlike
`home-assistant-mcp-server`/`voice-mcp-server`, which need real external credentials/endpoints).

## Network segmentation and encryption at rest

`project-tau-plan.md` calls for this server to live on its own VLAN with encryption at rest, since
face/voice embeddings are sensitive biometric data. That's a Phase 0 infra concern (network
segmentation, disk encryption on whatever host `MEMORY_DB_PATH` lives on) - this package doesn't
and can't enforce it in code, the same way CDG rules describe policy in `reason:` text rather than
implementing network controls themselves.

## Manual smoke test

No external service needed - just run it and call the tools directly the same way
`tau-core/tests/test_mcp_client_manager.py` does for the `echo` example server, using
`tau_core.mcp_client.MCPClientManager` with a `ServerConfig` pointing at
`python -m memory_mcp_server.server`.
