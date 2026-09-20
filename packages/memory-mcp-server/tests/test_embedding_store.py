import chromadb
import pytest

from memory_mcp_server.embedding_store import EmbeddingStore


def make_store(tmp_path) -> EmbeddingStore:
    # A tmp_path-backed PersistentClient gives each test a genuinely isolated store -
    # EphemeralClient() doesn't isolate collection state (including its locked embedding
    # dimension) between "separate" client instances within the same process.
    return EmbeddingStore(chromadb.PersistentClient(path=str(tmp_path)))


def test_match_on_empty_collection_returns_empty_list(tmp_path):
    store = make_store(tmp_path)

    assert store.match("face", [0.1, 0.2, 0.3]) == []


def test_match_face_returns_closest_person_first(tmp_path):
    store = make_store(tmp_path)
    store.store("face", "zion", [1.0, 0.0, 0.0])
    store.store("face", "guest", [0.0, 1.0, 0.0])

    matches = store.match("face", [0.9, 0.1, 0.0], top_k=2)

    assert matches[0]["person_id"] == "zion"
    assert len(matches) == 2


def test_voice_and_face_collections_are_independent(tmp_path):
    store = make_store(tmp_path)
    store.store("face", "zion", [1.0, 0.0, 0.0])

    # Only a face embedding was ever stored - the voice collection must still be empty.
    assert store.match("voice", [1.0, 0.0, 0.0]) == []


def test_store_supports_multiple_samples_per_person(tmp_path):
    store = make_store(tmp_path)
    store.store("face", "zion", [1.0, 0.0, 0.0])
    store.store("face", "zion", [0.9, 0.1, 0.0])

    matches = store.match("face", [0.95, 0.05, 0.0], top_k=2)

    assert {m["person_id"] for m in matches} == {"zion"}


def test_unknown_kind_raises(tmp_path):
    store = make_store(tmp_path)

    with pytest.raises(ValueError, match="unknown embedding kind"):
        store.match("fingerprint", [0.1])


def test_counts_by_person_on_empty_collection_returns_empty_dict(tmp_path):
    store = make_store(tmp_path)

    assert store.counts_by_person("face") == {}


def test_counts_by_person_counts_samples_per_person(tmp_path):
    store = make_store(tmp_path)
    store.store("face", "zion", [1.0, 0.0, 0.0])
    store.store("face", "zion", [0.9, 0.1, 0.0])
    store.store("face", "guest", [0.0, 1.0, 0.0])

    assert store.counts_by_person("face") == {"zion": 2, "guest": 1}


def test_counts_by_person_is_independent_per_kind(tmp_path):
    store = make_store(tmp_path)
    store.store("face", "zion", [1.0, 0.0, 0.0])

    assert store.counts_by_person("voice") == {}
