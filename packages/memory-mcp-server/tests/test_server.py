import pytest

from memory_mcp_server import server
from memory_mcp_server.memory_tree import MemoryNode


class FakeEmbeddingStore:
    def __init__(self, counts: dict[str, dict[str, int]] | None = None):
        self.stored: list[tuple[str, str, list[float]]] = []
        self.match_calls: list[tuple[str, list[float], int]] = []
        self.counts = counts or {"face": {}, "voice": {}}

    def store(self, kind, person_id, embedding):
        self.stored.append((kind, person_id, embedding))
        return f"{person_id}:fake-id"

    def match(self, kind, embedding, top_k=1):
        self.match_calls.append((kind, embedding, top_k))
        return [{"person_id": "zion", "distance": 0.01}]

    def counts_by_person(self, kind):
        return self.counts.get(kind, {})


class FakeProfileStore:
    def __init__(self):
        self.profiles: dict[str, dict] = {}
        self.upserted: list[str] = []

    def get(self, person_id):
        return self.profiles.get(person_id)

    def list_all(self):
        return dict(self.profiles)

    def upsert(self, person_id, **fields):
        self.upserted.append(person_id)
        profile = self.profiles.setdefault(person_id, {"access_level": "unknown"})
        profile.update(fields)
        return profile

    def set_access_level(self, person_id, access_level):
        return self.upsert(person_id, access_level=access_level)


class FakeMemoryTreeStore:
    def __init__(self):
        self.created: list[tuple] = []
        self.reinforced: list[str] = []
        self._nodes: dict[str, MemoryNode] = {}

    def create_node(self, title, content, parent_id=None, tags=None, status="approved", owner=""):
        self.created.append((title, content, parent_id, tags, owner))
        node = MemoryNode(
            id="node-1", title=title, content=content, parent_id=parent_id, tags=tags or [],
            status=status, owner=owner,
        )
        self._nodes[node.id] = node
        return node

    def search(self, query, limit=10, owner=None):
        self.last_search_owner = owner
        return [n for n in self._nodes.values() if query.lower() in n.content.lower()][:limit]

    def get_tree(self, root_id=None):
        return {"roots": [n.to_dict() for n in self._nodes.values()]}

    def reinforce(self, node_id, delta=0.5):
        self.reinforced.append(node_id)
        node = self._nodes[node_id]
        node.score += delta
        return node


def patch_stores(
    monkeypatch,
    embeddings: FakeEmbeddingStore,
    profiles: FakeProfileStore,
    memory_tree: "FakeMemoryTreeStore | None" = None,
) -> None:
    monkeypatch.setattr(server, "_embeddings", lambda: embeddings)
    monkeypatch.setattr(server, "_profiles", lambda: profiles)
    if memory_tree is not None:
        monkeypatch.setattr(server, "_memory_tree", lambda: memory_tree)


def test_store_face_stores_embedding_and_creates_profile(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    result = server.store_face("zion", [1.0, 0.0])

    assert result == {"embedding_id": "zion:fake-id", "person_id": "zion"}
    assert embeddings.stored == [("face", "zion", [1.0, 0.0])]
    assert profiles.get("zion") == {"access_level": "unknown"}


def test_store_voice_stores_embedding_and_creates_profile(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    result = server.store_voice("zion", [0.0, 1.0])

    assert result == {"embedding_id": "zion:fake-id", "person_id": "zion"}
    assert embeddings.stored == [("voice", "zion", [0.0, 1.0])]


def test_match_face_is_read_only(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    matches = server.match_face([0.9, 0.1], top_k=3)

    assert matches == [{"person_id": "zion", "distance": 0.01}]
    assert embeddings.match_calls == [("face", [0.9, 0.1], 3)]
    assert profiles.upserted == []


def test_match_voice_is_read_only(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    server.match_voice([0.1, 0.9])

    assert embeddings.match_calls == [("voice", [0.1, 0.9], 1)]
    assert profiles.upserted == []


def test_get_person_profile_returns_none_for_unknown_person(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    assert server.get_person_profile("nobody") is None


def test_set_access_level_updates_profile(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)
    profiles.upsert("zion")

    result = server.set_access_level("zion", "owner")

    assert result == {"access_level": "owner"}
    assert profiles.get("zion") == {"access_level": "owner"}


def test_list_people_combines_profiles_and_embedding_counts(monkeypatch):
    embeddings = FakeEmbeddingStore(counts={"face": {"zion": 2, "guest": 1}, "voice": {"zion": 1}})
    profiles = FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)
    profiles.upsert("zion", access_level="owner")
    profiles.upsert("guest", access_level="visitor")

    result = server.list_people()

    assert result == {
        "people": [
            {"person_id": "guest", "access_level": "visitor", "face_count": 1, "voice_count": 0},
            {"person_id": "zion", "access_level": "owner", "face_count": 2, "voice_count": 1},
        ]
    }


def test_list_people_includes_person_with_embeddings_but_no_profile(monkeypatch):
    embeddings = FakeEmbeddingStore(counts={"face": {"stray": 1}, "voice": {}})
    profiles = FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    result = server.list_people()

    assert result == {
        "people": [{"person_id": "stray", "access_level": "unknown", "face_count": 1, "voice_count": 0}]
    }


def test_list_people_on_empty_stores_returns_empty_list(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    assert server.list_people() == {"people": []}


def test_set_person_portrait_caches_svg_on_profile(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    result = server.set_person_portrait("zion", svg="<svg>...</svg>")

    assert result == {"access_level": "unknown", "portrait_svg": "<svg>...</svg>", "portrait_ascii": ""}
    assert profiles.get("zion") == result


def test_set_person_portrait_requires_svg_or_ascii(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)

    with pytest.raises(ValueError, match="requires at least one of svg or ascii_art"):
        server.set_person_portrait("zion")


def test_set_person_portrait_preserves_existing_access_level(monkeypatch):
    embeddings, profiles = FakeEmbeddingStore(), FakeProfileStore()
    patch_stores(monkeypatch, embeddings, profiles)
    profiles.upsert("zion", access_level="owner")

    result = server.set_person_portrait("zion", ascii_art="(o_o)")

    assert result["access_level"] == "owner"
    assert result["portrait_ascii"] == "(o_o)"


def test_store_memory_creates_node_with_parsed_tags(monkeypatch):
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)

    result = server.store_memory("Kickoff", "We decided X.", tags="project, decision")

    assert result["title"] == "Kickoff"
    assert tree.created == [("Kickoff", "We decided X.", None, ["project", "decision"], "")]


def test_store_memory_passes_through_parent_id(monkeypatch):
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)

    server.store_memory("Child", "content", parent_id="parent-1")

    assert tree.created == [("Child", "content", "parent-1", [], "")]


def test_search_memory_is_read_only(monkeypatch):
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)
    tree.create_node("Note", "shared keyword")

    results = server.search_memory("shared")

    assert len(results) == 1
    assert results[0]["content"] == "shared keyword"


def test_draft_memory_records_owner(monkeypatch):
    """Phase 13.5: the owner tau-core injects (the identified speaker) is stored on the draft."""
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)

    result = server.draft_memory("Lights", "prefers 40% after 22:00", owner="zion")

    assert result["owner"] == "zion"
    assert result["status"] == "draft"
    assert tree.created == [("Lights", "prefers 40% after 22:00", None, [], "zion")]


def test_search_memory_passes_owner_scope_to_store(monkeypatch):
    """The owner scope reaches the store, so recall is per-speaker, not global."""
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)

    server.search_memory("anything", owner="zion")

    assert tree.last_search_owner == "zion"


def test_get_memory_tree_returns_forest(monkeypatch):
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)
    tree.create_node("Note", "content")

    result = server.get_memory_tree()

    assert len(result["roots"]) == 1


def test_reinforce_memory_bumps_score(monkeypatch):
    embeddings, profiles, tree = FakeEmbeddingStore(), FakeProfileStore(), FakeMemoryTreeStore()
    patch_stores(monkeypatch, embeddings, profiles, tree)
    node = tree.create_node("Note", "content")

    result = server.reinforce_memory(node.id)

    assert result["score"] == 1.5
    assert tree.reinforced == [node.id]
