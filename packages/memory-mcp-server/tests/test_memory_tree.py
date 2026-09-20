import pytest

from memory_mcp_server.memory_tree import MemoryTreeStore, UnknownMemoryNodeError


@pytest.fixture
def store(tmp_path):
    return MemoryTreeStore(tmp_path / "tree.sqlite", tmp_path / "vault")


def test_create_node_returns_populated_node(store):
    node = store.create_node("Kickoff notes", "We decided X.", tags=["project", "decision"])

    assert node.title == "Kickoff notes"
    assert node.content == "We decided X."
    assert node.tags == ["project", "decision"]
    assert node.score == 1.0
    assert node.parent_id is None


def test_create_node_writes_a_markdown_file_to_the_vault(store, tmp_path):
    store.create_node("Kickoff notes", "We decided X.", tags=["project"])

    vault_files = list((tmp_path / "vault").glob("*.md"))
    assert len(vault_files) == 1
    text = vault_files[0].read_text(encoding="utf-8")
    assert "title: Kickoff notes" in text
    assert "We decided X." in text


def test_get_node_round_trips_content_without_frontmatter(store):
    created = store.create_node("Note", "Body text here.")

    fetched = store.get_node(created.id)

    assert fetched.content == "Body text here."
    assert fetched.title == "Note"


def test_get_node_returns_none_for_unknown_id(store):
    assert store.get_node("does-not-exist") is None


def test_create_node_rejects_unknown_parent(store):
    with pytest.raises(UnknownMemoryNodeError):
        store.create_node("Child", "content", parent_id="nonexistent")


def test_create_node_nests_under_real_parent(store):
    parent = store.create_node("Parent", "parent content")
    child = store.create_node("Child", "child content", parent_id=parent.id)

    assert child.parent_id == parent.id
    children = store.get_children(parent.id)
    assert [c.id for c in children] == [child.id]


def test_reinforce_increases_score_and_updates_timestamp(store):
    node = store.create_node("Note", "content")
    original_updated_at = node.updated_at

    reinforced = store.reinforce(node.id, delta=0.5)

    assert reinforced.score == 1.5
    assert reinforced.updated_at >= original_updated_at


def test_reinforce_unknown_node_raises(store):
    with pytest.raises(UnknownMemoryNodeError):
        store.reinforce("nonexistent")


def test_search_matches_title_tags_and_content(store):
    store.create_node("Kickoff notes", "We discussed the roadmap.", tags=["planning"])
    store.create_node("Unrelated", "Something else entirely.", tags=["misc"])

    by_title = store.search("kickoff")
    by_content = store.search("roadmap")
    by_tag = store.search("planning")

    assert len(by_title) == 1
    assert len(by_content) == 1
    assert len(by_tag) == 1
    assert by_title[0].title == "Kickoff notes"


def test_search_ranks_higher_scored_nodes_first(store):
    low = store.create_node("Match one", "shared keyword")
    high = store.create_node("Match two", "shared keyword")
    store.reinforce(high.id, delta=5.0)

    results = store.search("shared")

    assert results[0].id == high.id
    assert results[1].id == low.id


def test_search_respects_limit(store):
    for i in range(5):
        store.create_node(f"Note {i}", "shared keyword")

    results = store.search("shared", limit=2)

    assert len(results) == 2


def test_search_matches_any_term_not_verbatim_phrase(store):
    """Multi-term queries match nodes containing any term - conversational recall queries
    ("what did we decide about the kitchen lights") must not require the words verbatim in
    order, which the original whole-string substring search did."""
    store.create_node("Kitchen lighting decision", "Dim the kitchen lights to 40% after 22:00.")

    results = store.search("decide kitchen lights")

    assert len(results) == 1
    assert results[0].title == "Kitchen lighting decision"


def test_search_ranks_by_matched_term_count_before_score(store):
    """A node matching more distinct query terms outranks a higher-scored node matching fewer -
    relevance to this query beats general importance."""
    one_term = store.create_node("Printer notes", "The kitchen printer jams.")
    two_terms = store.create_node("Lighting notes", "The kitchen lights flicker.")
    store.reinforce(one_term.id, delta=5.0)

    results = store.search("kitchen lights")

    assert [n.id for n in results] == [two_terms.id, one_term.id]


def test_search_empty_query_returns_nothing(store):
    store.create_node("Note", "content")

    assert store.search("   ") == []


def test_search_ignores_stopwords_and_short_terms(store):
    """Live testing showed 'is'/'the'-class terms recalling every memory on every turn -
    "what is 12 times 8?" must not recall the kitchen-lighting decision."""
    store.create_node("Kitchen lighting decision", "Dim the kitchen lights to 40% after 22:00.")

    assert store.search("what is 12 times 8?") == []


def test_search_matches_at_word_boundaries_with_prefix(store):
    """A term matches any word it prefixes ('light' finds both 'lighting' and 'lights'), but a
    term buried inside another word must not match - 'rot' should not recall 'carrot'."""
    store.create_node("Kitchen lighting decision", "Dim them after 22:00.")
    store.create_node("Garden notes", "Planted carrot seeds this week.")

    by_prefix = store.search("light")
    buried = store.search("rot")

    assert [n.title for n in by_prefix] == ["Kitchen lighting decision"]
    assert buried == []


@pytest.fixture
def fts_store(tmp_path):
    # fts_threshold=0 forces every search() call through the FTS5 candidate-narrowing path
    # (see MemoryTreeStore._candidate_rows) instead of the full-table-scan default, so these
    # tests actually exercise it rather than relying on real deployments ever having 500+ nodes.
    return MemoryTreeStore(tmp_path / "tree.sqlite", tmp_path / "vault", fts_threshold=0)


def test_fts_candidate_path_matches_full_scan_results(fts_store):
    """Same scenario as test_search_ranks_by_matched_term_count_before_score, run through the
    FTS-narrowed path - the candidate prefilter must not change which nodes are found or how
    they rank, only how many rows get regex-verified to produce that answer."""
    one_term = fts_store.create_node("Printer notes", "The kitchen printer jams.")
    two_terms = fts_store.create_node("Lighting notes", "The kitchen lights flicker.")
    fts_store.reinforce(one_term.id, delta=5.0)

    results = fts_store.search("kitchen lights")

    assert [n.id for n in results] == [two_terms.id, one_term.id]


def test_fts_candidate_path_respects_word_boundary(fts_store):
    """FTS5's own token-prefix match is a superset filter; the regex re-check downstream must
    still reject 'rot' matching inside 'carrot' even when FTS handed it back as a candidate."""
    fts_store.create_node("Kitchen lighting decision", "Dim them after 22:00.")
    fts_store.create_node("Garden notes", "Planted carrot seeds this week.")

    by_prefix = fts_store.search("light")
    buried = fts_store.search("rot")

    assert [n.title for n in by_prefix] == ["Kitchen lighting decision"]
    assert buried == []


def test_fts_index_survives_reopening_the_store(tmp_path):
    """A backfill runs on open if memory_fts has fewer rows than memory_nodes - proves an
    existing pre-FTS database (or any partial-write scenario) gets indexed on the next open,
    not silently left with the old nodes invisible to the candidate path forever."""
    db_path = tmp_path / "tree.sqlite"
    vault_path = tmp_path / "vault"
    first = MemoryTreeStore(db_path, vault_path)
    node = first.create_node("Old note", "written before a restart")
    first.close()

    # Simulate an upgrade from a pre-FTS database: drop the index but keep the row, then reopen.
    import sqlite3

    conn = sqlite3.connect(db_path)
    conn.execute("DROP TABLE memory_fts")
    conn.commit()
    conn.close()

    reopened = MemoryTreeStore(db_path, vault_path, fts_threshold=0)
    results = reopened.search("written")

    assert [n.id for n in results] == [node.id]


def test_get_tree_builds_nested_forest(store):
    parent = store.create_node("Parent", "p")
    child = store.create_node("Child", "c", parent_id=parent.id)
    grandchild = store.create_node("Grandchild", "g", parent_id=child.id)

    tree = store.get_tree()

    assert len(tree["roots"]) == 1
    root = tree["roots"][0]
    assert root["id"] == parent.id
    assert root["children"][0]["id"] == child.id
    assert root["children"][0]["children"][0]["id"] == grandchild.id


def test_get_tree_with_root_id_returns_subtree_only(store):
    parent = store.create_node("Parent", "p")
    child = store.create_node("Child", "c", parent_id=parent.id)

    subtree = store.get_tree(root_id=child.id)

    assert subtree["id"] == child.id
    assert "roots" not in subtree


def test_get_tree_unknown_root_raises(store):
    with pytest.raises(UnknownMemoryNodeError):
        store.get_tree(root_id="nonexistent")


def test_top_scored_orders_by_score_descending(store):
    a = store.create_node("A", "a")
    b = store.create_node("B", "b")
    store.reinforce(a.id, delta=10.0)

    top = store.top_scored(limit=2)

    assert [n.id for n in top] == [a.id, b.id]


# --- Phase 13.5: per-speaker owner scoping ---------------------------------------------------


def test_owner_persists_on_node_and_in_frontmatter(store, tmp_path):
    node = store.create_node("Lights", "prefers 40% dim", owner="zion")
    assert node.owner == "zion"
    assert store.get_node(node.id).owner == "zion"
    md = next((tmp_path / "vault").glob("*.md")).read_text(encoding="utf-8")
    assert "owner: zion" in md


def test_owner_defaults_to_unattributed(store):
    node = store.create_node("Shared", "household fact")
    assert node.owner == ""


def test_search_scoped_to_owner_returns_own_and_shared_not_others(store):
    store.create_node("Zion note", "kitchen codeword falcon", owner="zion")
    store.create_node("Amara note", "study codeword falcon", owner="amara")
    store.create_node("Shared note", "hallway codeword falcon", owner="")

    zion_hits = {n.title for n in store.search("codeword", owner="zion")}
    # Zion recalls own + shared, never amara's.
    assert zion_hits == {"Zion note", "Shared note"}


def test_search_unknown_speaker_sees_only_shared(store):
    store.create_node("Zion note", "codeword falcon", owner="zion")
    store.create_node("Shared note", "codeword falcon", owner="")

    hits = {n.title for n in store.search("codeword", owner="")}
    assert hits == {"Shared note"}


def test_search_owner_none_is_unscoped(store):
    store.create_node("Zion note", "codeword falcon", owner="zion")
    store.create_node("Amara note", "codeword falcon", owner="amara")

    # None = admin/debug: everything, regardless of owner.
    assert len(store.search("codeword", owner=None)) == 2


def test_promotion_preserves_owner(store):
    from memory_mcp_server.memory_tree import STATUS_APPROVED, STATUS_DRAFT

    draft = store.create_node("Lights", "prefers 40%", status=STATUS_DRAFT, owner="zion")
    promoted = store.set_status(draft.id, STATUS_APPROVED)
    # "Everything per-speaker": approving a person's draft doesn't hand it to the household.
    assert promoted.owner == "zion"
    assert promoted.status == STATUS_APPROVED


def test_owner_migration_backfills_existing_rows_as_shared(tmp_path):
    """A pre-per-speaker DB (no owner column) must open cleanly, its rows readable as shared."""
    import sqlite3

    db = tmp_path / "legacy.sqlite"
    vault = tmp_path / "vault"
    vault.mkdir()
    md = vault / "old-abc123.md"
    md.write_text("---\nid: abc123\ntitle: Old\ntags: []\n---\n\nlegacy body", encoding="utf-8")
    conn = sqlite3.connect(db)
    # Schema without status OR owner, as an old vault would have.
    conn.execute(
        "CREATE TABLE memory_nodes (id TEXT PRIMARY KEY, title TEXT NOT NULL, parent_id TEXT, "
        "tags TEXT NOT NULL DEFAULT '', score REAL NOT NULL DEFAULT 1.0, content_path TEXT NOT NULL, "
        "created_at REAL NOT NULL, updated_at REAL NOT NULL)"
    )
    conn.execute(
        "INSERT INTO memory_nodes VALUES (?,?,?,?,?,?,?,?)",
        ("abc123", "Old", None, "", 1.0, str(md), 1.0, 1.0),
    )
    conn.commit()
    conn.close()

    store = MemoryTreeStore(db, vault)
    node = store.get_node("abc123")
    assert node.owner == ""  # unattributed = shared
    # And it's recallable by anyone, since shared nodes surface for every speaker.
    assert node.id in {n.id for n in store.search("legacy", owner="zion")}


# --- Phase 15 #3: owner case-normalization at the store boundary ------------------------------


def test_owner_is_normalized_to_lowercase_on_write(store):
    """The store must agree with tau-core's chat path (`speaker.strip().lower()`) on casing, or a
    memory written as "Zion" would be invisible to the lowercased recall path."""
    node = store.create_node("Lights", "prefers 40% dim", owner="  Zion  ")
    assert node.owner == "zion"
    assert store.get_node(node.id).owner == "zion"


def test_recall_matches_regardless_of_owner_casing(store):
    """A differently-cased owner on either side (write or search) must still match - no caller
    can strand a memory by disagreeing about "Zion" vs "zion"."""
    store.create_node("Zion note", "codeword falcon", owner="Zion")

    assert {n.title for n in store.search("codeword", owner="zion")} == {"Zion note"}
    assert {n.title for n in store.search("codeword", owner="ZION")} == {"Zion note"}
