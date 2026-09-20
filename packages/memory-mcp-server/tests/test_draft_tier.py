"""Phase 8.B: the unverified draft tier.

project-tau-plan.md §7.3 declined silent auto-memory writes and said the middle path - "an
unverified draft tier, batch-reviewed" - needed designing before building. These tests pin the
properties that design rests on. The one that matters most: a draft can be written freely
BECAUSE it can never quietly become a fact.
"""

import pytest

from memory_mcp_server import server as srv
from memory_mcp_server.memory_tree import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    MemoryTreeStore,
    UnknownMemoryNodeError,
)


@pytest.fixture
def store(tmp_path) -> MemoryTreeStore:
    return MemoryTreeStore(db_path=tmp_path / "memory.db", vault_path=tmp_path / "vault")


@pytest.fixture
def tools(monkeypatch, store) -> MemoryTreeStore:
    """Exercises the MCP tool functions against a REAL store.

    These exist because the store-level tests below missed a bug that live-driving caught
    immediately: `promote_memory` referenced STATUS_APPROVED, which server.py never imported, so
    the review gate - the single most important part of this design - raised NameError on every
    call. Testing MemoryTreeStore.set_status directly could never have seen it; the defect was in
    the tool wrapper, which nothing tested.

    Worse, the failure was invisible from the outside: FastMCP turns a tool exception into an
    error *string* inside a successful result, so the CDG and the audit trail both recorded the
    broken gate as `outcome: executed`. A tool that fails loudly is fine; one that fails while
    reporting success is how a governance gate rots without anyone noticing.
    """
    monkeypatch.setattr(srv, "_memory_tree", lambda: store)
    return store


def test_create_node_defaults_to_approved(store):
    """store_memory's path must not become a draft by accident - it is CDG-gated, so anything
    created through it genuinely was signed off by a human."""
    node = store.create_node("Kitchen lighting", "Dim to 40% after 22:00")
    assert node.status == STATUS_APPROVED
    assert not node.is_draft


def test_draft_is_marked_and_not_approved(store):
    node = store.create_node("Guess", "They might like tea", status=STATUS_DRAFT)
    assert node.is_draft
    assert node.to_dict()["status"] == STATUS_DRAFT


def test_status_is_written_into_the_vault_frontmatter(store):
    """The vault is meant to be opened in Obsidian. Someone reading a note Tau wrote about them
    has to be able to see, in the note, whether anyone ever checked it."""
    node = store.create_node("Guess", "They might like tea", status=STATUS_DRAFT)
    text = next((store.vault_path).glob(f"*{node.id}.md")).read_text(encoding="utf-8")
    assert "status: draft" in text


def test_promotion_updates_both_the_index_and_the_vault_file(store):
    """A vault claiming `status: draft` for a promoted memory would be worse than no label."""
    node = store.create_node("Guess", "They like tea", status=STATUS_DRAFT)

    promoted = store.set_status(node.id, STATUS_APPROVED)

    assert promoted.status == STATUS_APPROVED
    assert store.get_node(node.id).status == STATUS_APPROVED
    text = next((store.vault_path).glob(f"*{node.id}.md")).read_text(encoding="utf-8")
    assert "status: approved" in text
    assert "status: draft" not in text


def test_promotion_preserves_content_and_tags(store):
    node = store.create_node("T", "Body text", tags=["a", "b"], status=STATUS_DRAFT)
    promoted = store.set_status(node.id, STATUS_APPROVED)
    assert promoted.content == "Body text"
    assert promoted.tags == ["a", "b"]


def test_invalid_status_is_rejected(store):
    with pytest.raises(ValueError, match="status must be one of"):
        store.create_node("T", "C", status="totally-legit")
    node = store.create_node("T2", "C")
    with pytest.raises(ValueError):
        store.set_status(node.id, "verified-trust-me")


def test_list_drafts_is_oldest_first_and_excludes_approved(store):
    """A review queue should surface what has been waiting longest, not the newest guess."""
    first = store.create_node("First guess", "x", status=STATUS_DRAFT)
    second = store.create_node("Second guess", "y", status=STATUS_DRAFT)
    store.create_node("Real memory", "z")

    drafts = store.list_by_status(STATUS_DRAFT)

    assert [d.id for d in drafts] == [first.id, second.id]


def test_search_ranks_approved_above_drafts_at_equal_match(store):
    """When a human-checked memory and a thing Tau inferred both match, the checked one is what
    Tau should reach for."""
    store.create_node("Kettle guess", "Maybe they use the kettle at 7am", status=STATUS_DRAFT)
    approved = store.create_node("Kettle fact", "The kettle is descaled monthly")

    results = store.search("kettle")

    assert results[0].id == approved.id
    assert {r.status for r in results} == {STATUS_APPROVED, STATUS_DRAFT}


def test_search_can_be_restricted_to_a_tier(store):
    store.create_node("Kettle guess", "maybe", status=STATUS_DRAFT)
    store.create_node("Kettle fact", "definitely")

    approved_only = store.search("kettle", statuses=(STATUS_APPROVED,))

    assert [n.status for n in approved_only] == [STATUS_APPROVED]


def test_deleting_a_draft_removes_its_file_too(store):
    node = store.create_node("Wrong", "bad guess", status=STATUS_DRAFT)
    path = next((store.vault_path).glob(f"*{node.id}.md"))

    store.delete_node(node.id)

    assert store.get_node(node.id) is None
    assert not path.exists()


def test_deleting_a_node_reparents_its_children_rather_than_orphaning_them(store):
    """Discarding one wrong observation must never silently take unrelated memories with it."""
    root = store.create_node("Root", "r")
    middle = store.create_node("Middle", "m", parent_id=root.id, status=STATUS_DRAFT)
    child = store.create_node("Child", "c", parent_id=middle.id)

    store.delete_node(middle.id)

    assert store.get_node(child.id) is not None
    assert store.get_node(child.id).parent_id == root.id


def test_deleting_an_unknown_node_raises(store):
    with pytest.raises(UnknownMemoryNodeError):
        store.delete_node("nope")


# --- the MCP tools themselves (not just the store underneath them) ----------------------------


def test_draft_memory_tool_creates_a_draft(tools):
    result = srv.draft_memory("Kitchen lighting", "Dim after 22:00", tags="preferences, lighting")

    assert result["status"] == STATUS_DRAFT
    assert result["tags"] == ["preferences", "lighting"]
    assert tools.get_node(result["id"]).is_draft


def test_promote_memory_tool_actually_promotes(tools):
    """The regression test for the NameError that live-driving caught: this exact call returned
    'Error executing tool promote_memory: name STATUS_APPROVED is not defined' - reported to the
    caller as a SUCCESSFUL tool result - so the review gate was entirely non-functional while
    looking fine."""
    draft = srv.draft_memory("Guess", "They might like tea")

    result = srv.promote_memory(draft["id"])

    assert result["status"] == STATUS_APPROVED
    assert not tools.get_node(draft["id"]).is_draft


def test_promoted_memory_leaves_the_review_queue(tools):
    draft = srv.draft_memory("Guess", "content")
    assert [d["id"] for d in srv.list_drafts()] == [draft["id"]]

    srv.promote_memory(draft["id"])

    assert srv.list_drafts() == []


def test_list_drafts_tool_excludes_approved_memories(tools):
    srv.store_memory("Real memory", "human-approved")
    draft = srv.draft_memory("Guess", "unchecked")

    assert [d["id"] for d in srv.list_drafts()] == [draft["id"]]


def test_discard_draft_tool_removes_it(tools):
    draft = srv.draft_memory("Wrong", "bad guess")

    result = srv.discard_draft(draft["id"])

    assert result["discarded"] == draft["id"]
    assert tools.get_node(draft["id"]) is None


def test_discard_draft_refuses_to_delete_an_approved_memory(tools):
    """Governance: discard_draft is ungated precisely BECAUSE it can only remove things no human
    vouched for. If it could delete approved memories, an ungated tool would be able to destroy
    human-checked records - so this refusal is what justifies the missing approval gate."""
    approved = srv.store_memory("Real decision", "We chose X because Y")

    with pytest.raises(ValueError, match="approved memory, not a draft"):
        srv.discard_draft(approved["id"])

    assert tools.get_node(approved["id"]) is not None


def test_discard_draft_on_an_unknown_node_raises(tools):
    with pytest.raises(UnknownMemoryNodeError):
        srv.discard_draft("no-such-node")


def test_store_memory_tool_still_creates_approved_memories(tools):
    """Phase 8 must not have quietly turned the CDG-gated path into a draft."""
    assert srv.store_memory("Real", "content")["status"] == STATUS_APPROVED


def test_search_memory_tool_reports_status_so_recall_can_label_it(tools):
    """tau-core's recall marks drafts '[unverified draft]' from this field. If the tool stopped
    returning it, drafts would silently start reading as facts in the model's prompt."""
    srv.draft_memory("Kettle guess", "maybe 7am")

    results = srv.search_memory("kettle")

    assert results[0]["status"] == STATUS_DRAFT


def test_existing_vaults_migrate_with_their_memories_still_approved(tmp_path):
    """A pre-Phase-8 database has no status column. Its nodes must come back APPROVED: the only
    way to create one back then was the CDG-gated store_memory, so they really were signed off.
    Backfilling them as drafts would dump real memories into a review queue and invite someone
    to rubber-stamp them."""
    import sqlite3

    db_path = tmp_path / "old.db"
    vault = tmp_path / "vault"
    vault.mkdir()
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE memory_nodes (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, parent_id TEXT,
            tags TEXT NOT NULL DEFAULT '', score REAL NOT NULL DEFAULT 1.0,
            content_path TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
        )
        """
    )
    old_file = vault / "legacy-abc123.md"
    old_file.write_text("---\nid: abc123\ntitle: Legacy\ntags: []\n---\n\nA real decision.")
    conn.execute(
        "INSERT INTO memory_nodes VALUES ('abc123','Legacy',NULL,'',1.0,?,1.0,1.0)",
        (str(old_file),),
    )
    conn.commit()
    conn.close()

    store = MemoryTreeStore(db_path=db_path, vault_path=vault)

    node = store.get_node("abc123")
    assert node.status == STATUS_APPROVED
    assert "A real decision." in node.content
    assert store.list_by_status(STATUS_DRAFT) == []
