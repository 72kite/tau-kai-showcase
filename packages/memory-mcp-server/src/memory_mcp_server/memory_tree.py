"""Memory Tree Engine - Phase 2.8 (OpenHuman-derived; see project-tau-plan.md).

Complements EmbeddingStore, it doesn't replace it. Face/voice data is genuinely embedding-
shaped (fixed-length vectors, similarity search is the right tool) and stays on Chroma
unchanged. Project/conversational context is a different shape of problem: OpenHuman's audit
(see memory record `openhuman-audit-and-integration`) found that for this kind of memory,
plain interpretable Markdown nodes beat vector embeddings - a human (or Tau) can read, edit,
and directly reason about a memory node's content, which an embedding can't offer. Each node
is also written out as a real `.md` file under a vault directory, so the tree doubles as a
browsable Obsidian vault, not just something queryable through MCP tools.

SQLite holds the structural index (parent/child links, score, tags, timestamps) so search and
tree traversal don't require re-parsing every Markdown file on every call; the Markdown files
remain the human-readable source of truth for content.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_SCORE = 1.0
DEFAULT_REINFORCE_DELTA = 0.5

# Phase 8.B - the "unverified draft tier" project-tau-plan.md §7.3 said had to be designed
# before it was built.
#
# APPROVED: a human explicitly signed off on this memory (store_memory is CDG-gated, exactly as
#   face/voice enrolment is). This is the only tier that existed before Phase 8.
# DRAFT: Tau wrote this by itself, from a conversation, without asking. Nobody has checked it.
#
# The tiers exist because auto-memory has two failure modes and they need different answers.
# "Tau writes down something wrong" is survivable if the wrongness is visible and correctable -
# that is what DRAFT is: labelled, listable, greppable, and rendered in the Obsidian vault with
# `status: draft` in its frontmatter, so a human reading the vault always knows which claims Tau
# invented about them. "Tau *acts* on something wrong it inferred" is not survivable the same
# way, which is why promotion to APPROVED is CDG-gated and why nothing that grants authority
# (access tiers, approvals) reads this store at all.
STATUS_APPROVED = "approved"
STATUS_DRAFT = "draft"
VALID_STATUSES = (STATUS_APPROVED, STATUS_DRAFT)


def _slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "untitled"


class UnknownMemoryNodeError(ValueError):
    pass


# Phase 13.5 (§10.1 #3, the per-speaker half): every node carries the identified speaker it
# belongs to, so one person's memories - drafts AND approved - don't surface for another. Empty
# means "unattributed": either the speaker was unknown (voice ID failed, or a typed turn) or the
# node predates this field. Unattributed nodes are shared (visible to everyone), which is the
# right reading of legacy data - a memory created before per-speaker scoping belonged to the
# household, not to nobody. See MemoryTreeStore.search for the recall rule.
UNOWNED = ""


@dataclass
class MemoryNode:
    id: str
    title: str
    content: str
    parent_id: str | None
    tags: list[str] = field(default_factory=list)
    score: float = DEFAULT_SCORE
    created_at: float = 0.0
    updated_at: float = 0.0
    status: str = STATUS_APPROVED
    owner: str = UNOWNED

    @property
    def is_draft(self) -> bool:
        return self.status == STATUS_DRAFT

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "parent_id": self.parent_id,
            "tags": self.tags,
            "score": self.score,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            # Always present in the payload, never inferred by the caller: every consumer of a
            # memory needs to know whether a human ever checked it.
            "status": self.status,
            # The identified speaker this memory belongs to ("" = unattributed/shared). Present so
            # the admin review queue can show whose inference a draft is (list_drafts stays
            # unscoped for review), and so recall can be reasoned about.
            "owner": self.owner,
        }


class MemoryTreeStore:
    # search() below does an exact, interpretable regex match over every candidate row (see its
    # own docstring for why - "matched 2 of 3 terms" has to stay a claim a human can verify).
    # Below this many rows, a full table scan is fast enough that adding a second index just to
    # skip it would be all risk (a tokenizer mismatch between FTS5 and the regex below could
    # silently drop a real match) for no measurable gain - a home deployment's memory tree isn't
    # going to hit this size soon. Above it, FTS5 narrows the candidate set (rows containing at
    # least one query term as a token prefix) before the same regex verification runs over just
    # those rows, which is the actual fix for "loads every row into Python" at the scale where
    # that would ever start to hurt. Overridable in tests so this path is exercised without
    # needing thousands of real rows.
    FTS_CANDIDATE_THRESHOLD = 500

    def __init__(
        self, db_path: str | Path, vault_path: str | Path, fts_threshold: int | None = None
    ):
        self.db_path = Path(db_path)
        self.vault_path = Path(vault_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.vault_path.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._fts_threshold = (
            fts_threshold if fts_threshold is not None else self.FTS_CANDIDATE_THRESHOLD
        )
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS memory_nodes (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                parent_id TEXT,
                tags TEXT NOT NULL DEFAULT '',
                score REAL NOT NULL DEFAULT 1.0,
                content_path TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                status TEXT NOT NULL DEFAULT '{STATUS_APPROVED}',
                owner TEXT NOT NULL DEFAULT '{UNOWNED}'
            )
            """
        )
        self._migrate_add_status()
        self._migrate_add_owner()
        self._init_fts()
        self._conn.commit()

    def _init_fts(self) -> None:
        """A standalone (not `content=`-linked) FTS5 index over id/title/tags/content, since
        content itself lives in Markdown files, not this table - kept in sync by create_node/
        delete_node rather than SQLite triggers, because content is set once at creation and
        never mutated in place (there is no update_node), so there is exactly one write path to
        keep in sync per row's lifetime.
        """
        self._conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, text)")
        indexed = self._conn.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
        total = self._conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        if indexed < total:
            # Upgrading a database that predates this index (or one FTS insert failed to commit
            # alongside its row somehow) - backfill whatever's missing rather than assuming
            # "some rows indexed" means "all rows indexed".
            indexed_ids = {r[0] for r in self._conn.execute("SELECT id FROM memory_fts")}
            for row in self._conn.execute("SELECT * FROM memory_nodes"):
                if row["id"] not in indexed_ids:
                    node = self._row_to_node(row)
                    self._index_fts(node.id, node.title, node.tags, node.content)

    def _index_fts(self, node_id: str, title: str, tags: list[str], content: str) -> None:
        self._conn.execute(
            "INSERT INTO memory_fts (id, text) VALUES (?, ?)",
            (node_id, f"{title} {' '.join(tags)} {content}"),
        )

    def _migrate_add_status(self) -> None:
        """Adds `status` to a pre-Phase-8 database.

        Existing nodes default to APPROVED, and that is the correct reading of history rather
        than a convenient one: before Phase 8 the only way to create a node was `store_memory`,
        which is CDG-gated, so every node already in a vault genuinely was signed off by a human.
        Backfilling them as drafts would be the unsafe direction - it would dump a real approved
        memory into a review queue and invite someone to rubber-stamp it.
        """
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(memory_nodes)")}
        if "status" not in columns:
            self._conn.execute(
                f"ALTER TABLE memory_nodes ADD COLUMN status TEXT NOT NULL DEFAULT '{STATUS_APPROVED}'"
            )

    def _migrate_add_owner(self) -> None:
        """Adds `owner` to a pre-per-speaker database (Phase 13.5).

        Existing nodes default to UNOWNED (shared), which is the honest reading of history: a
        memory created before per-speaker scoping was never attributed to one person, so it
        belongs to the household. Backfilling them to some arbitrary speaker would be the unsafe
        direction - it would hide existing memories from everyone except one guessed owner.
        """
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(memory_nodes)")}
        if "owner" not in columns:
            self._conn.execute(
                f"ALTER TABLE memory_nodes ADD COLUMN owner TEXT NOT NULL DEFAULT '{UNOWNED}'"
            )

    def create_node(
        self,
        title: str,
        content: str,
        parent_id: str | None = None,
        tags: list[str] | None = None,
        status: str = STATUS_APPROVED,
        owner: str = UNOWNED,
    ) -> MemoryNode:
        if parent_id is not None and self._get_row(parent_id) is None:
            raise UnknownMemoryNodeError(f"Unknown parent_id: {parent_id!r}")
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")

        node_id = uuid.uuid4().hex[:12]
        now = time.time()
        tags = tags or []
        # Normalize owner at the store boundary (Phase 15 #3), matching tau-core's chat path
        # (`speaker.strip().lower()`): every caller must agree on "Zion" vs "zion", or a memory
        # written under one casing would be invisible to the lowercased recall path.
        owner = (owner or UNOWNED).strip().lower()
        content_path = self._write_markdown(node_id, title, content, tags, status, owner)

        self._conn.execute(
            "INSERT INTO memory_nodes "
            "(id, title, parent_id, tags, score, content_path, created_at, updated_at, status, owner) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, title, parent_id, ",".join(tags), DEFAULT_SCORE, str(content_path), now, now, status, owner),
        )
        self._index_fts(node_id, title, tags, content)
        self._conn.commit()
        return MemoryNode(node_id, title, content, parent_id, tags, DEFAULT_SCORE, now, now, status, owner)

    def set_status(self, node_id: str, status: str) -> MemoryNode:
        """Promote a draft to approved (or demote). Rewrites the Markdown so the vault's
        frontmatter never disagrees with the index - the file is the human-readable source of
        truth, and a vault claiming `status: draft` for an approved memory would be worse than
        no label at all."""
        if status not in VALID_STATUSES:
            raise ValueError(f"status must be one of {VALID_STATUSES}, got {status!r}")
        row = self._get_row(node_id)
        if row is None:
            raise UnknownMemoryNodeError(f"Unknown node_id: {node_id!r}")
        node = self._row_to_node(row)
        now = time.time()
        # Owner is preserved across promotion (Phase 13.5): a person's draft, once approved, is
        # still that person's memory - "everything per-speaker" means promotion doesn't hand a
        # private inference to the whole household.
        self._write_markdown(node_id, node.title, node.content, node.tags, status, node.owner)
        self._conn.execute(
            "UPDATE memory_nodes SET status = ?, updated_at = ? WHERE id = ?", (status, now, node_id)
        )
        self._conn.commit()
        return self.get_node(node_id)

    def delete_node(self, node_id: str) -> None:
        """Removes a node and its Markdown file. Children are re-parented to this node's parent
        rather than silently deleted: discarding one wrong observation must never take unrelated
        memories with it."""
        row = self._get_row(node_id)
        if row is None:
            raise UnknownMemoryNodeError(f"Unknown node_id: {node_id!r}")
        self._conn.execute(
            "UPDATE memory_nodes SET parent_id = ? WHERE parent_id = ?", (row["parent_id"], node_id)
        )
        self._conn.execute("DELETE FROM memory_nodes WHERE id = ?", (node_id,))
        self._conn.execute("DELETE FROM memory_fts WHERE id = ?", (node_id,))
        self._conn.commit()
        path = Path(row["content_path"])
        if path.exists():
            path.unlink()

    def list_by_status(self, status: str, limit: int = 50) -> list[MemoryNode]:
        """Oldest first - a review queue should surface what has been waiting longest, not what
        Tau happened to think most recently."""
        rows = self._conn.execute(
            "SELECT * FROM memory_nodes WHERE status = ? ORDER BY created_at ASC LIMIT ?",
            (status, limit),
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def _write_markdown(
        self,
        node_id: str,
        title: str,
        content: str,
        tags: list[str],
        status: str = STATUS_APPROVED,
        owner: str = UNOWNED,
    ) -> Path:
        # `status` and `owner` are in the frontmatter, not just the SQLite index, because the
        # vault is meant to be opened and read by a human in Obsidian. Someone reading a note Tau
        # wrote needs to see, in the note, whether anyone checked it (status) and whose memory it
        # is (owner) - a blank owner reads as household/shared.
        frontmatter = (
            f"---\nid: {node_id}\ntitle: {title}\ntags: [{', '.join(tags)}]\n"
            f"status: {status}\nowner: {owner}\n---\n\n"
        )
        path = self.vault_path / f"{_slugify(title)}-{node_id}.md"
        path.write_text(frontmatter + content, encoding="utf-8")
        return path

    def _get_row(self, node_id: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM memory_nodes WHERE id = ?", (node_id,)).fetchone()

    def _row_to_node(self, row: sqlite3.Row) -> MemoryNode:
        content_path = Path(row["content_path"])
        content = content_path.read_text(encoding="utf-8") if content_path.exists() else ""
        # Strip the frontmatter block back out so callers get back what they put in, not the
        # persisted-with-frontmatter representation.
        if content.startswith("---\n"):
            end = content.find("\n---\n\n", 4)
            if end != -1:
                content = content[end + 6 :]
        tags = [t for t in row["tags"].split(",") if t]
        keys = row.keys()
        return MemoryNode(
            row["id"],
            row["title"],
            content,
            row["parent_id"],
            tags,
            row["score"],
            row["created_at"],
            row["updated_at"],
            # A row read before the migration ran (or from an old backup restored underneath a
            # live store) has no status column; treat it as approved, matching _migrate_add_status.
            row["status"] if "status" in keys else STATUS_APPROVED,
            # Likewise a pre-per-speaker row has no owner column; treat it as unowned/shared.
            row["owner"] if "owner" in keys else UNOWNED,
        )

    def get_node(self, node_id: str) -> MemoryNode | None:
        row = self._get_row(node_id)
        return self._row_to_node(row) if row else None

    def reinforce(self, node_id: str, delta: float = DEFAULT_REINFORCE_DELTA) -> MemoryNode:
        """Bump a node's score - it was relevant, accessed, or confirmed accurate. This is the
        Memory Tree's substitute for embedding-space similarity ranking: score is a plain
        number a human can see and understand, not a distance in a latent space.
        """
        row = self._get_row(node_id)
        if row is None:
            raise UnknownMemoryNodeError(f"Unknown node_id: {node_id!r}")
        now = time.time()
        new_score = row["score"] + delta
        self._conn.execute(
            "UPDATE memory_nodes SET score = ?, updated_at = ? WHERE id = ?", (new_score, now, node_id)
        )
        self._conn.commit()
        return self.get_node(node_id)

    # Common words that carry no retrieval signal. A query term this common matching a node
    # says nothing about relevance - live testing (2026-07-11) showed "is"/"the"-class terms
    # recalling every stored memory on every conversational turn, including for "what is 12
    # times 8?".
    _STOPWORDS = frozenset(
        "the and for are was were what did does about that this with have has had you your "
        "our their there here when where which will would could should can from into onto "
        "than then them they its it's how why who all any not now get set".split()
    )

    def _candidate_rows(self, terms: list[str]) -> list[sqlite3.Row]:
        """Rows search() needs to regex-verify. Below FTS_CANDIDATE_THRESHOLD total nodes, that's
        just every row, unchanged from before this index existed. Above it, FTS5 narrows this to
        rows containing at least one term as a token prefix - a strict superset of what the
        regex loop below would keep, since a real match always means some indexed token starts
        with that term too. Any FTS failure (a query-syntax edge case, an un-backfilled table)
        falls back to the full scan rather than risk silently dropping a real match.
        """
        total = self._conn.execute("SELECT COUNT(*) FROM memory_nodes").fetchone()[0]
        if total <= self._fts_threshold:
            return self._conn.execute(
                "SELECT * FROM memory_nodes ORDER BY score DESC, updated_at DESC"
            ).fetchall()
        try:
            match_query = " OR ".join(f"{term}*" for term in terms)
            ids = [
                r[0] for r in self._conn.execute(
                    "SELECT id FROM memory_fts WHERE memory_fts MATCH ?", (match_query,)
                )
            ]
            if not ids:
                return []
            placeholders = ",".join("?" * len(ids))
            return self._conn.execute(
                f"SELECT * FROM memory_nodes WHERE id IN ({placeholders}) "
                "ORDER BY score DESC, updated_at DESC",
                ids,
            ).fetchall()
        except sqlite3.OperationalError:
            return self._conn.execute(
                "SELECT * FROM memory_nodes ORDER BY score DESC, updated_at DESC"
            ).fetchall()

    def search(
        self,
        query: str,
        limit: int = 10,
        statuses: tuple[str, ...] | None = None,
        owner: str | None = None,
    ) -> list[MemoryNode]:
        """Term-based search over title/tags/content - a node matches if any significant query
        term appears in it at a word boundary (prefix match, so "lights" finds "lighting"),
        ranked by how many distinct terms matched, then verified-before-unverified, then score,
        then recency. Terms shorter than 3 characters and stopwords are ignored: they match
        everything and mean nothing. Still interpretable by design (no embedding math to explain
        when Tau cites a memory: "matched 2 of 3 terms" is an explanation a human can verify by
        reading the node).

        `statuses` restricts which tiers are searched (default: all). Approved nodes outrank
        drafts at equal match count, deliberately: when a human-checked memory and a thing Tau
        inferred on its own both match, the checked one should be what Tau reaches for.

        `owner` scopes recall to one speaker (Phase 13.5, "everything per-speaker"): `None` means
        no scoping (admin/debug/tests see everything); a value returns only nodes owned by that
        speaker OR unattributed/shared nodes (`owner == ""`). So an identified person recalls
        their own memories plus household-shared ones, and never another person's - drafts and
        approved memories alike. An unknown speaker (owner="") recalls only shared nodes.
        """
        # Normalize the scope owner the same way the store boundary does (Phase 15 #3), so recall
        # matches memories written under any casing. None stays None (no scoping - admin/tests).
        if owner is not None:
            owner = owner.strip().lower()
        terms = [
            t for t in re.findall(r"[a-z0-9]+", query.lower())
            if len(t) >= 3 and t not in self._STOPWORDS
        ]
        if not terms:
            return []
        patterns = [re.compile(r"\b" + re.escape(term)) for term in terms]
        rows = self._candidate_rows(terms)
        scored: list[tuple[int, int, int, MemoryNode]] = []
        for position, row in enumerate(rows):
            node = self._row_to_node(row)
            if statuses is not None and node.status not in statuses:
                continue
            # Per-speaker scoping: your own memories plus shared/unattributed ones, never someone
            # else's. UNOWNED nodes are shared, so legacy data and unknown-speaker drafts stay
            # visible to everyone rather than getting orphaned.
            if owner is not None and node.owner != owner and node.owner != UNOWNED:
                continue
            haystack = f"{node.title} {' '.join(node.tags)} {node.content}".lower()
            matched = sum(1 for pattern in patterns if pattern.search(haystack))
            if matched:
                # position preserves the score-then-recency SQL ordering within a match count
                scored.append((matched, 1 if node.is_draft else 0, position, node))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [node for _, _, _, node in scored[:limit]]

    def get_children(self, parent_id: str | None) -> list[MemoryNode]:
        rows = self._conn.execute(
            "SELECT * FROM memory_nodes WHERE parent_id IS ? ORDER BY score DESC", (parent_id,)
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def get_tree(self, root_id: str | None = None) -> dict[str, Any]:
        """Nested tree structure starting at root_id, or the full forest of top-level nodes if
        root_id is None."""
        if root_id is None:
            roots = self.get_children(None)
            return {"roots": [self._build_subtree(r) for r in roots]}
        root = self.get_node(root_id)
        if root is None:
            raise UnknownMemoryNodeError(f"Unknown node_id: {root_id!r}")
        return self._build_subtree(root)

    def _build_subtree(self, node: MemoryNode) -> dict[str, Any]:
        d = node.to_dict()
        d["children"] = [self._build_subtree(c) for c in self.get_children(node.id)]
        return d

    def top_scored(self, limit: int = 10) -> list[MemoryNode]:
        rows = self._conn.execute("SELECT * FROM memory_nodes ORDER BY score DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_node(r) for r in rows]

    def close(self) -> None:
        self._conn.close()
