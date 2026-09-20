"""Multi-user voice identity through the web bridge: speaker identification riding along with
transcription, double-approval-gated voiceprint enrollment, and replay-resistant
challenge-response verification for approval decisions.

The MCP transport is faked (canned per-tool responses); the CDG, approval queue, and bridge
orchestration all run for real - these tests prove the identity flows respect the same gates
as everything else, not that speechbrain works.
"""

import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from mcp.types import CallToolResult, TextContent

from tau_core.config import TauCoreSettings
from tau_core.host import TauCoreHost
from tau_core.web.identity import ChallengeStore, phrase_match_score
from tau_core.web.server import create_app

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

EMBEDDING = [0.1, 0.2, 0.3, 0.4]


class FakeManager:
    """Stands in for MCPClientManager: canned JSON per (server, tool), overridable per test."""

    def __init__(self):
        self.calls = []
        self.responses = {
            ("voice-mcp-server", "transcribe"): "what did we decide",
            ("voice-mcp-server", "identify_speaker"): {"embedding": EMBEDDING, "embedding_dim": 4},
            ("voice-mcp-server", "enroll_voiceprint"): {"person_id": "zion", "embedding": EMBEDDING},
            ("memory-mcp-server", "match_voice"): [{"person_id": "zion", "distance": 0.3}],
            ("memory-mcp-server", "get_person_profile"): {"person_id": "zion", "access_level": "admin"},
            ("memory-mcp-server", "store_voice"): {"embedding_id": "zion:abc", "person_id": "zion"},
            ("voice-mcp-server", "detect_wake_word"): {
                "detected": True, "score": 0.83, "model": "hey_jarvis", "threshold": 0.5
            },
            # A JSON-encoded STRING, not a raw list - list_wake_words really returns one string
            # (one content block) whose text is the whole array, unlike match_voice below, which
            # really does return one content block per match. A raw list here would hit
            # FakeManager's one-block-per-item branch and silently test the wrong shape.
            ("voice-mcp-server", "list_wake_words"): json.dumps([
                {"id": "hey_tau", "label": "Hey Tau", "current": True},
                {"id": "hey_jarvis", "label": "Hey Jarvis", "current": False},
            ]),
            ("voice-mcp-server", "set_wake_word"): {
                "wake_id": "hey_jarvis", "label": "Hey Jarvis", "model": "hey_jarvis"
            },
            # JSON-encoded string for the same reason as list_wake_words above.
            ("voice-mcp-server", "list_voices"): json.dumps([
                {"id": "en_US-lessac-medium", "label": "Lessac - US English, neutral",
                 "current": True, "available": True},
                {"id": "en_US-amy-medium", "label": "Amy - US English, warmer",
                 "current": False, "available": False},
            ]),
            ("voice-mcp-server", "set_voice"): {
                "voice_id": "en_US-amy-medium", "label": "Amy - US English, warmer"
            },
        }

    def connected_servers(self):
        return ["voice-mcp-server", "memory-mcp-server"]

    async def call_tool(self, server, tool, arguments):
        self.calls.append((server, tool, arguments))
        payload = self.responses[(server, tool)]
        if isinstance(payload, list):
            content = [TextContent(type="text", text=json.dumps(item)) for item in payload]
        elif isinstance(payload, str):
            content = [TextContent(type="text", text=payload)]
        else:
            content = [TextContent(type="text", text=json.dumps(payload))]
        return CallToolResult(content=content)


def make_app(require_voice_approval=False):
    settings = TauCoreSettings(
        cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml",
        require_voice_approval=require_voice_approval,
    )
    manager = FakeManager()
    host = TauCoreHost(manager, settings=settings)
    app = create_app(settings=settings, host=host)
    return app, host, manager


def client_for(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_transcribe_identifies_speaker():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        resp = await client.post("/api/voice/transcribe", json={"audio_b64": "QUJD"})

    body = resp.json()
    assert body["text"] == "what did we decide"
    assert body["speaker"] == {"person_id": "zion", "distance": 0.3, "access_level": "admin"}


async def test_wake_endpoint_returns_detection():
    """The frontend polls /api/voice/wake in local mode; it should surface openWakeWord's
    detection verbatim so the hook can fire invoke on `detected`."""
    app, _host, manager = make_app()
    async with client_for(app) as client:
        resp = await client.post("/api/voice/wake", json={"audio_b64": "QUJD"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["detected"] is True
    assert body["score"] == 0.83
    assert ("voice-mcp-server", "detect_wake_word", {"audio_base64": "QUJD"}) in manager.calls


async def test_list_wake_words_route_returns_listing():
    app, _host, manager = make_app()
    async with client_for(app) as client:
        resp = await client.get("/api/voice/wake-words")
    assert resp.status_code == 200
    body = resp.json()
    assert {"id": "hey_tau", "label": "Hey Tau", "current": True} in body
    assert ("voice-mcp-server", "list_wake_words", {}) in manager.calls


async def test_set_wake_word_requires_approval_then_executes():
    """set_wake_word is CDG-gated (cdg_rules.yaml's voice-set-wake-word-needs-approval) - a
    settings-picker POST must come back pending, then execute on retry once a human approves,
    the same continuation shape as /api/voice/enroll's two-stage flow."""
    app, host, manager = make_app()
    async with client_for(app) as client:
        r1 = await client.post("/api/voice/wake-words", json={"wake_id": "hey_jarvis"})
        b1 = r1.json()
        assert b1["status"] == "pending_approval"
        assert ("voice-mcp-server", "set_wake_word", {"wake_id": "hey_jarvis"}) not in manager.calls
        approval_id = b1["approval_request_id"]

        host.approvals.approve(approval_id, "test-human")

        r2 = await client.post(
            "/api/voice/wake-words",
            json={"wake_id": "hey_jarvis", "approval_request_id": approval_id},
        )
        b2 = r2.json()
    assert b2 == {"status": "ok", "wake_id": "hey_jarvis", "label": "Hey Jarvis", "model": "hey_jarvis"}
    assert ("voice-mcp-server", "set_wake_word", {"wake_id": "hey_jarvis"}) in manager.calls


async def test_list_voices_route_returns_listing():
    app, _host, manager = make_app()
    async with client_for(app) as client:
        resp = await client.get("/api/voice/voices")
    assert resp.status_code == 200
    body = resp.json()
    assert {"id": "en_US-lessac-medium", "label": "Lessac - US English, neutral",
            "current": True, "available": True} in body
    # The picker needs to know a voice has no model on disk, or someone selects it and quietly
    # gets a different voice.
    assert any(row["available"] is False for row in body)
    assert ("voice-mcp-server", "list_voices", {}) in manager.calls


async def test_set_voice_requires_approval_then_executes():
    """set_voice is CDG-gated (voice-set-tts-voice-needs-approval) for the same reason
    set_wake_word is: it changes how Tau sounds on every device in the household, so an admin
    tapping SELECT is not by itself a human approving the change."""
    app, host, manager = make_app()
    async with client_for(app) as client:
        r1 = await client.post("/api/voice/voices", json={"voice_id": "en_US-amy-medium"})
        b1 = r1.json()
        assert b1["status"] == "pending_approval"
        assert ("voice-mcp-server", "set_voice", {"voice_id": "en_US-amy-medium"}) not in manager.calls
        approval_id = b1["approval_request_id"]

        host.approvals.approve(approval_id, "test-human")

        r2 = await client.post(
            "/api/voice/voices",
            json={"voice_id": "en_US-amy-medium", "approval_request_id": approval_id},
        )
        b2 = r2.json()
    assert b2 == {"status": "ok", "voice_id": "en_US-amy-medium", "label": "Amy - US English, warmer"}
    assert ("voice-mcp-server", "set_voice", {"voice_id": "en_US-amy-medium"}) in manager.calls


async def test_wake_words_routes_require_admin_when_voice_approval_is_on():
    """Admin-gated like the rest of the settings surface (device list, activity feed) - separate
    from the CDG gate above, which fires regardless of who's asking."""
    app, _host, _manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        r1 = await client.get("/api/voice/wake-words")
        r2 = await client.post("/api/voice/wake-words", json={"wake_id": "hey_jarvis"})
    assert r1.status_code == 428
    assert r2.status_code == 428


async def test_transcribe_far_voice_match_is_unknown_speaker():
    """A match beyond the distance ceiling must come back as unknown (null), never a guess."""
    app, _host, manager = make_app()
    manager.responses[("memory-mcp-server", "match_voice")] = [{"person_id": "zion", "distance": 5.0}]
    async with client_for(app) as client:
        resp = await client.post("/api/voice/transcribe", json={"audio_b64": "QUJD"})

    assert resp.json()["speaker"] is None


async def test_enrollment_passes_through_both_cdg_gates():
    """enroll_voiceprint AND store_voice each require human approval (cdg_rules.yaml). The
    two-stage endpoint must surface both gates and complete only after both sign-offs."""
    app, host, _manager = make_app()
    async with client_for(app) as client:
        # Stage 1: blocked at the first gate.
        r1 = await client.post("/api/voice/enroll", json={"person_id": "Zion", "audio_b64": "QUJD"})
        b1 = r1.json()
        assert b1["status"] == "pending_enroll"
        approval_1 = b1["approval_request_id"]

        host.approvals.approve(approval_1, "test-human")

        # Stage 1 retry: embedding computed, blocked at the second gate.
        r2 = await client.post(
            "/api/voice/enroll",
            json={"person_id": "Zion", "audio_b64": "QUJD", "enroll_approval_id": approval_1},
        )
        b2 = r2.json()
        assert b2["status"] == "pending_store"
        assert b2["embedding"] == EMBEDDING
        approval_2 = b2["approval_request_id"]

        host.approvals.approve(approval_2, "test-human")

        # Stage 2: stored.
        r3 = await client.post(
            "/api/voice/enroll",
            json={"person_id": "Zion", "embedding": b2["embedding"], "store_approval_id": approval_2},
        )
        assert r3.json() == {"status": "enrolled", "person_id": "zion"}


async def test_challenge_flow_gates_approvals_when_enabled():
    app, host, manager = make_app(require_voice_approval=True)
    pending = host.approvals.submit(
        server="dangerous", tool="shutdown_host", arguments={"vmid": 1},
        reason="test", requested_by="test",
    )

    async with client_for(app) as client:
        # No token -> 428, the UI's cue to run the challenge flow.
        r = await client.post(f"/api/approvals/{pending.id}/approve", json={"decided_by": "tablet"})
        assert r.status_code == 428

        challenge = (await client.post("/api/voice/challenge")).json()
        # The "spoken" audio transcribes to exactly the challenge phrase.
        manager.responses[("voice-mcp-server", "transcribe")] = challenge["phrase"]

        verify = (
            await client.post(f"/api/voice/challenge/{challenge['challenge_id']}/verify", json={"audio_b64": "QUJD"})
        ).json()
        assert verify["verified"] is True
        assert verify["person_id"] == "zion"

        ok = await client.post(
            f"/api/approvals/{pending.id}/approve",
            json={"decided_by": "tablet"},
            headers={"X-Tau-Voice-Token": verify["voice_token"]},
        )
        assert ok.status_code == 200
        assert ok.json()["decided_by"] == "voice:zion"

        # Tokens are single-use: a second decision with the same token is refused.
        again = await client.post(
            f"/api/approvals/{pending.id}/deny",
            json={"decided_by": "tablet"},
            headers={"X-Tau-Voice-Token": verify["voice_token"]},
        )
        assert again.status_code == 428


async def test_challenge_rejects_wrong_phrase():
    """Replay defense: audio of the right person saying the WRONG words (i.e. any recording
    made before the challenge existed) must not verify."""
    app, _host, manager = make_app(require_voice_approval=True)
    async with client_for(app) as client:
        challenge = (await client.post("/api/voice/challenge")).json()
        manager.responses[("voice-mcp-server", "transcribe")] = "hey tau turn on the lights"

        verify = (
            await client.post(f"/api/voice/challenge/{challenge['challenge_id']}/verify", json={"audio_b64": "QUJD"})
        ).json()

    assert verify["verified"] is False
    assert "phrase mismatch" in verify["reason"]


async def test_challenge_rejects_unenrolled_voice():
    """Right words, unknown voice (someone else reading the phrase aloud) must not verify."""
    app, _host, manager = make_app(require_voice_approval=True)
    manager.responses[("memory-mcp-server", "match_voice")] = []
    async with client_for(app) as client:
        challenge = (await client.post("/api/voice/challenge")).json()
        manager.responses[("voice-mcp-server", "transcribe")] = challenge["phrase"]

        verify = (
            await client.post(f"/api/voice/challenge/{challenge['challenge_id']}/verify", json={"audio_b64": "QUJD"})
        ).json()

    assert verify["verified"] is False


async def test_speak_returns_audio_for_voice_out():
    """Phase 13 voice-out: /api/voice/speak turns reply text into base64 WAV via
    voice-mcp-server.speak, through the audited host.call_tool path (speak is CDG-allow)."""
    app, _host, manager = make_app()
    manager.responses[("voice-mcp-server", "speak")] = {"audio_base64": "QUJD", "format": "wav"}
    async with client_for(app) as client:
        resp = await client.post("/api/voice/speak", json={"text": "Kitchen lights are on."})
    assert resp.status_code == 200
    assert resp.json() == {"audio_base64": "QUJD", "format": "wav"}
    assert ("voice-mcp-server", "speak", {"text": "Kitchen lights are on."}) in manager.calls


async def test_speak_degrades_to_503_when_tts_unavailable():
    """TTS is best-effort: if voice-mcp-server/Piper can't answer (here: no canned speak
    response, standing in for the server being down), the endpoint returns a clean, sanitized
    503 - the text reply was already delivered by /api/chat, so speaking failing is not a turn
    failing."""
    app, _host, _manager = make_app()  # speak intentionally absent from FakeManager.responses
    async with client_for(app) as client:
        resp = await client.post("/api/voice/speak", json={"text": "hello"})
    assert resp.status_code == 503
    # Sanitized: the generic message, never the raw exception text.
    assert resp.json()["detail"] == "Text-to-speech is unavailable."


async def test_speak_rejects_empty_text():
    app, _host, _manager = make_app()
    async with client_for(app) as client:
        resp = await client.post("/api/voice/speak", json={"text": "   "})
    assert resp.status_code == 400


async def test_speak_shares_the_voice_rate_limiter():
    """Speak rides the voice limiter (a per-turn follow-up call, higher budget than chat), not a
    new one - a runaway/flooding client is capped like the other voice endpoints."""
    settings = TauCoreSettings(cdg_rules_path=CONFIG_DIR / "cdg_rules.yaml", voice_rate_limit_max=1)
    manager = FakeManager()
    manager.responses[("voice-mcp-server", "speak")] = {"audio_base64": "QUJD", "format": "wav"}
    host = TauCoreHost(manager, settings=settings)
    app = create_app(settings=settings, host=host)
    async with client_for(app) as client:
        first = await client.post("/api/voice/speak", json={"text": "one"})
        second = await client.post("/api/voice/speak", json={"text": "two"})
    assert first.status_code == 200
    assert second.status_code == 429
    assert "Retry-After" in second.headers


def test_phrase_match_score_tolerates_one_dropped_word():
    assert phrase_match_score("amber breeze copper delta", "Amber... breeze copper, delta!") == 1.0
    assert phrase_match_score("amber breeze copper delta", "amber breeze copper") == 0.75
    assert phrase_match_score("amber breeze copper delta", "totally unrelated words") == 0.0


def test_challenge_store_tokens_are_single_use():
    store = ChallengeStore()
    challenge_id, _phrase = store.create()
    token = store.mark_verified(challenge_id, "zion", "admin")

    assert store.consume_token(token) == ("zion", "admin")
    assert store.consume_token(token) is None
    assert store.consume_token("nonsense") is None


async def _verify_as(client, manager, profile_access_level):
    """Run the challenge flow so the verified speaker carries the given access level, and return
    the resulting single-use voice token."""
    manager.responses[("memory-mcp-server", "get_person_profile")] = {
        "person_id": "zion", "access_level": profile_access_level
    }
    challenge = (await client.post("/api/voice/challenge")).json()
    manager.responses[("voice-mcp-server", "transcribe")] = challenge["phrase"]
    verify = (
        await client.post(
            f"/api/voice/challenge/{challenge['challenge_id']}/verify", json={"audio_b64": "QUJD"}
        )
    ).json()
    assert verify["verified"] is True
    return verify["voice_token"]


async def test_low_tier_identity_cannot_approve_elevated_action():
    """The canonical rule: a 'kid'-tier identity may not approve exiting lockdown, even with a
    fully valid, live, replay-resistant voice challenge - identity is proven, tier is not."""
    app, host, manager = make_app(require_voice_approval=True)
    pending = host.approvals.submit(
        server="security-mcp-server", tool="exit_lockdown", arguments={"token": "x"},
        reason="test", requested_by="test",
    )
    async with client_for(app) as client:
        token = await _verify_as(client, manager, "kid")
        r = await client.post(
            f"/api/approvals/{pending.id}/approve",
            json={"decided_by": "tablet"},
            headers={"X-Tau-Voice-Token": token},
        )
    assert r.status_code == 403
    assert "requires 'admin'" in r.json()["detail"]
    # And the action stays pending - a rejected tier check must not decide it.
    assert host.approvals.get(pending.id).status.value == "pending"


async def test_admin_tier_identity_can_approve_elevated_action():
    app, host, manager = make_app(require_voice_approval=True)
    pending = host.approvals.submit(
        server="security-mcp-server", tool="exit_lockdown", arguments={"token": "x"},
        reason="test", requested_by="test",
    )
    async with client_for(app) as client:
        token = await _verify_as(client, manager, "admin")
        r = await client.post(
            f"/api/approvals/{pending.id}/approve",
            json={"decided_by": "tablet"},
            headers={"X-Tau-Voice-Token": token},
        )
    assert r.status_code == 200
    assert r.json()["decided_by"] == "voice:zion"


async def test_low_tier_identity_can_still_approve_routine_action():
    """Tiers gate only elevated actions: a 'kid' can approve a print job (not in the elevated
    policy) so enabling voice approval doesn't lock non-admins out of everything."""
    app, host, manager = make_app(require_voice_approval=True)
    pending = host.approvals.submit(
        server="fabrication-mcp-server", tool="submit_print_job", arguments={"file": "a.gcode"},
        reason="test", requested_by="test",
    )
    async with client_for(app) as client:
        token = await _verify_as(client, manager, "kid")
        r = await client.post(
            f"/api/approvals/{pending.id}/approve",
            json={"decided_by": "tablet"},
            headers={"X-Tau-Voice-Token": token},
        )
    assert r.status_code == 200
    assert r.json()["decided_by"] == "voice:zion"
