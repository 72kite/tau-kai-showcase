"""Memory MCP server (Phase 2 of project-tau-plan.md, Section 5.4): face/voice embedding storage
and per-person profiles/access levels.

Embeddings are computed elsewhere (vision-mcp-server for faces, voice-mcp-server for voices) -
this server only stores and searches already-computed vectors, never raw images/audio.

Run directly for manual testing:
    python -m memory_mcp_server.server
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import chromadb
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from memory_mcp_server.embedding_store import EmbeddingStore
from memory_mcp_server.memory_tree import (
    STATUS_APPROVED,
    STATUS_DRAFT,
    MemoryTreeStore,
    UnknownMemoryNodeError,
)
from memory_mcp_server.profile_store import DEFAULT_ACCESS_LEVEL, ProfileStore

load_dotenv()

mcp = FastMCP(
    "memory",
    host=os.getenv("FASTMCP_HOST", "127.0.0.1"),
    port=int(os.getenv("FASTMCP_PORT", "8000")),
)


def _embeddings() -> EmbeddingStore:
    db_path = os.environ.get("MEMORY_DB_PATH", "./data/chroma")
    return EmbeddingStore(chromadb.PersistentClient(path=db_path))


def _profiles() -> ProfileStore:
    profiles_path = Path(os.environ.get("MEMORY_PROFILES_PATH", "./data/profiles.json"))
    return ProfileStore(profiles_path)


def _memory_tree() -> MemoryTreeStore:
    db_path = os.environ.get("MEMORY_TREE_DB_PATH", "./data/memory_tree.sqlite")
    vault_path = os.environ.get("MEMORY_VAULT_PATH", "./data/memory_vault")
    return MemoryTreeStore(db_path, vault_path)


@mcp.tool()
def store_face(person_id: str, embedding: list[float]) -> dict[str, Any]:
    """Store a face embedding for person_id (embedding computed elsewhere, e.g.
    vision-mcp-server). Enrolling a new person requires human approval (see
    tau-core/config/cdg_rules.yaml).
    """
    embedding_id = _embeddings().store("face", person_id, embedding)
    _profiles().upsert(person_id)
    return {"embedding_id": embedding_id, "person_id": person_id}


@mcp.tool()
def match_face(embedding: list[float], top_k: int = 1) -> list[dict[str, Any]]:
    """Find the closest stored face embeddings to the given embedding."""
    return _embeddings().match("face", embedding, top_k)


@mcp.tool()
def store_voice(person_id: str, embedding: list[float]) -> dict[str, Any]:
    """Store a voice embedding for person_id (embedding computed elsewhere, e.g.
    voice-mcp-server). Enrolling a new person requires human approval (see
    tau-core/config/cdg_rules.yaml).
    """
    embedding_id = _embeddings().store("voice", person_id, embedding)
    _profiles().upsert(person_id)
    return {"embedding_id": embedding_id, "person_id": person_id}


@mcp.tool()
def match_voice(embedding: list[float], top_k: int = 1) -> list[dict[str, Any]]:
    """Find the closest stored voice embeddings to the given embedding."""
    return _embeddings().match("voice", embedding, top_k)


@mcp.tool()
def get_person_profile(person_id: str) -> dict[str, Any] | None:
    """Get a person's stored profile (access level, etc.)."""
    return _profiles().get(person_id)


@mcp.tool()
def set_access_level(person_id: str, access_level: str) -> dict[str, Any]:
    """Set a person's access level. Requires human approval (see
    tau-core/config/cdg_rules.yaml).
    """
    return _profiles().set_access_level(person_id, access_level)


@mcp.tool()
def list_people() -> dict[str, Any]:
    """List every known person profile with face/voice sample counts. Read-only - browsing
    who Tau already recognizes needs no approval; enrolling someone new (store_face/
    store_voice) still does.

    Returns {"people": [{person_id, access_level, face_count, voice_count}, ...]}. Wrapped in
    a dict (not a bare list) because FastMCP serializes each list item as its own content
    block - a caller that only reads the first block, like tau-core's web bridge, would
    otherwise see just one person.
    """
    profiles = _profiles().list_all()
    embeddings = _embeddings()
    face_counts = embeddings.counts_by_person("face")
    voice_counts = embeddings.counts_by_person("voice")
    person_ids = set(profiles) | set(face_counts) | set(voice_counts)
    people = [
        {
            "person_id": person_id,
            "access_level": profiles.get(person_id, {}).get("access_level", DEFAULT_ACCESS_LEVEL),
            "face_count": face_counts.get(person_id, 0),
            "voice_count": voice_counts.get(person_id, 0),
        }
        for person_id in sorted(person_ids)
    ]
    return {"people": people}


@mcp.tool()
def set_person_portrait(person_id: str, svg: str = "", ascii_art: str = "") -> dict[str, Any]:
    """Cache a person's e-ink-style portrait drawing so it never has to be redrawn on future
    recognitions. Call this once, right after Tau draws someone's portrait for the first time
    (see ui-bridge-mcp-server__update_recognition_state); get_person_profile then returns
    portrait_svg/portrait_ascii for instant reuse next time that person is recognized.

    Follows the same e-ink convention as design sketches: black strokes only, no
    fill/gradient/shadow, self-contained (no external references). Cosmetic metadata derived
    from an already-enrolled person, not new biometric data or an access change - unlike
    store_face/store_voice/set_access_level, this does not require approval.
    """
    if not svg and not ascii_art:
        raise ValueError("set_person_portrait requires at least one of svg or ascii_art")
    return _profiles().upsert(person_id, portrait_svg=svg, portrait_ascii=ascii_art)


@mcp.tool()
def store_memory(
    title: str, content: str, parent_id: str = "", tags: str = "", owner: str = ""
) -> dict[str, Any]:
    """Store a new Memory Tree node - project/conversational context, not biometric data (see
    store_face/store_voice for that). Requires human approval (see
    tau-core/config/cdg_rules.yaml).

    title: short heading for this memory
    content: Markdown body
    parent_id: optional existing node id to nest this memory under
    tags: optional comma-separated tags
    owner: the identified speaker this memory belongs to; "" = shared/household. Set server-side
        by tau-core from the voice-identified speaker, not by the model - see Phase 13.5.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    node = _memory_tree().create_node(title, content, parent_id or None, tag_list, owner=owner)
    return node.to_dict()


@mcp.tool()
def search_memory(query: str, limit: int = 10, owner: str = "") -> list[dict[str, Any]]:
    """Search Memory Tree nodes by keyword over title/tags/content. Read-only.

    owner scopes recall to one speaker (Phase 13.5): returns only that speaker's memories plus
    shared/household ones (owner=""), never another person's. Set server-side by tau-core from the
    voice-identified speaker; "" (the default, and an unknown speaker) recalls only shared nodes.
    """
    return [n.to_dict() for n in _memory_tree().search(query, limit, owner=owner)]


@mcp.tool()
def get_memory_tree(root_id: str = "") -> dict[str, Any]:
    """Get the Memory Tree (or the subtree rooted at root_id) as nested JSON. Read-only."""
    return _memory_tree().get_tree(root_id or None)


@mcp.tool()
def reinforce_memory(node_id: str) -> dict[str, Any]:
    """Bump a Memory Tree node's score - it was relevant, accessed, or confirmed accurate.
    Read-write but safe: it only adjusts an existing node's score, never creates or deletes
    content, so it does not require approval.
    """
    return _memory_tree().reinforce(node_id).to_dict()


# --- Phase 8.B: the unverified draft tier -----------------------------------------------------
#
# project-tau-plan.md §7.3 declined silent auto-memory, saying the middle path - "auto-write to
# an unverified draft tier, batch-reviewed" - needed designing before building. This is that
# design. The shape of it: writing is cheap and reversible, so it is ungated; *believing* is
# expensive, so promotion is gated exactly like store_memory always was.


@mcp.tool()
def draft_memory(
    title: str, content: str, parent_id: str = "", tags: str = "", owner: str = ""
) -> dict[str, Any]:
    """Remember a fact about the user or their home for later, as an UNVERIFIED draft.

    Use this whenever the user says "remember that...", "don't forget...", "note that...", or
    tells you a lasting preference, routine, name, or decision - even when the sentence is also
    ABOUT a device. "Remember that I like the kitchen lights at 40% after 10pm" is a memory to
    record, not a light to change; the user is telling you something, not asking you to act.

    That distinction is the point of the first two lines. In the 2026-07-22 eval this exact
    prompt failed: the model saw "kitchen lights" and went looking for a scheduling function,
    replying "we would need a function to configure or schedule lighting settings". The verb the
    user actually used ("Remember") is the signal, and it now leads the description instead of
    sitting three lines down under "Record something you noticed".

    Examples: "prefers the kitchen lights dim after 22:00", "the 3D printer is called Bertha",
    "works from home on Thursdays". No approval needed - drafts are clearly labelled as
    unchecked, are never treated as fact, and a human can promote or discard them later.

    Only record things worth recalling in a future conversation. Do not draft the conversation
    itself, one-off requests, or anything the user asked you to forget. Prefer one specific
    durable fact per draft over a summary of the chat.

    title: short heading, e.g. "Kitchen lighting preference"
    content: the observation, in plain Markdown, phrased so a human can check it
    parent_id: optional existing node id to nest under
    tags: optional comma-separated tags
    owner: the identified speaker this draft is about; "" = shared. Set server-side by tau-core
        from the voice-identified speaker (Phase 13.5), so one person's inferences don't surface
        for another - not something the model chooses.
    """
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    node = _memory_tree().create_node(
        title, content, parent_id or None, tag_list, status=STATUS_DRAFT, owner=owner
    )
    return node.to_dict()


@mcp.tool()
def list_drafts(limit: int = 50) -> list[dict[str, Any]]:
    """List unverified draft memories awaiting human review, oldest first. Read-only.

    This is the batch-review queue: drafts deliberately do NOT go into the approval queue when
    written (auto-writing every turn would spam it - the objection that got auto-memory declined
    in the first place). They accumulate here instead and are reviewed together.
    """
    return [n.to_dict() for n in _memory_tree().list_by_status(STATUS_DRAFT, limit)]


@mcp.tool()
def promote_memory(node_id: str) -> dict[str, Any]:
    """Promote an unverified draft to an approved memory. Requires human approval (see
    tau-core/config/cdg_rules.yaml) - this is the review gate, and the reason drafts are safe
    to write freely: nothing Tau invents about someone becomes a checked fact without a human
    saying so, at the same bar as store_memory itself.
    """
    return _memory_tree().set_status(node_id, STATUS_APPROVED).to_dict()


@mcp.tool()
def discard_draft(node_id: str) -> dict[str, Any]:
    """Delete an unverified draft memory - it was wrong, or not worth keeping.

    No approval needed: this only ever removes something no human vouched for, which is the
    conservative direction (an approval gate on forgetting a wrong guess about someone would be
    an obstacle to correcting Tau, not a safety feature). Refuses to touch approved memories -
    deleting a human-checked memory is not this tool's job.
    """
    store = _memory_tree()
    node = store.get_node(node_id)
    if node is None:
        raise UnknownMemoryNodeError(f"Unknown node_id: {node_id!r}")
    if not node.is_draft:
        raise ValueError(
            f"Node {node_id!r} is an approved memory, not a draft; discard_draft only removes "
            "unverified drafts."
        )
    store.delete_node(node_id)
    return {"discarded": node_id, "title": node.title}


if __name__ == "__main__":
    mcp.run(transport=os.getenv("MCP_TRANSPORT", "stdio"))
