from __future__ import annotations

import uuid
from typing import Any

from chromadb.api import ClientAPI

KINDS = {"face", "voice"}


class EmbeddingStore:
    """Wraps two Chroma collections ("faces", "voices") for similarity storage/lookup. Stores no
    profile data itself - just person_id -> embedding - so profile fields (access level, etc.)
    live in one place (ProfileStore), not duplicated across every embedding a person has.

    Embeddings are computed elsewhere (vision-mcp-server for faces, voice-mcp-server for voices) -
    this class only ever sees already-computed vectors.
    """

    def __init__(self, client: ClientAPI):
        self._collections = {
            "face": client.get_or_create_collection("faces"),
            "voice": client.get_or_create_collection("voices"),
        }

    def _collection(self, kind: str):
        if kind not in KINDS:
            raise ValueError(f"unknown embedding kind '{kind}', expected one of {sorted(KINDS)}")
        return self._collections[kind]

    def store(self, kind: str, person_id: str, embedding: list[float]) -> str:
        collection = self._collection(kind)
        embedding_id = f"{person_id}:{uuid.uuid4().hex}"
        collection.add(ids=[embedding_id], embeddings=[embedding], metadatas=[{"person_id": person_id}])
        return embedding_id

    def match(self, kind: str, embedding: list[float], top_k: int = 1) -> list[dict[str, Any]]:
        collection = self._collection(kind)
        count = collection.count()
        if count == 0:
            return []

        results = collection.query(query_embeddings=[embedding], n_results=min(top_k, count))
        return [
            {"person_id": metadata["person_id"], "distance": distance}
            for metadata, distance in zip(results["metadatas"][0], results["distances"][0])
        ]

    def counts_by_person(self, kind: str) -> dict[str, int]:
        """Number of stored embeddings per person_id for one kind ("face" or "voice")."""
        collection = self._collection(kind)
        metadatas = collection.get(include=["metadatas"])["metadatas"] or []
        counts: dict[str, int] = {}
        for metadata in metadatas:
            person_id = metadata["person_id"]
            counts[person_id] = counts.get(person_id, 0) + 1
        return counts
