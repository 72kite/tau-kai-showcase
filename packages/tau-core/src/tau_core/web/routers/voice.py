"""Voice pipeline routes: transcribe, wake-word detection/settings, TTS voice settings, speak,
voiceprint enrollment, and the replay-resistant identity challenge.

Split out of web/server.py's create_app() in Phase 49. `_identify_speaker` is specific to this
domain (used by voice_transcribe and voice_challenge_verify only), unlike the deps.py helpers.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from tau_core.cdg import CoreDirectiveViolation
from tau_core.host import ToolCallStatus
from tau_core.web.deps import (
    MAX_ATTACHMENT_B64_CHARS,
    SharedDeps,
    _client_key,
    _device_id,
    _enforce_rate_limit,
    _require_admin,
    _sanitized_error,
    _tool_json,
)
from tau_core.web.identity import WORD_MATCH_THRESHOLD, phrase_match_score

logger = logging.getLogger(__name__)

# The most reply text /api/voice/speak will synthesize (Phase 13, voice-out). A spoken answer is
# meant to be short (the prompt shapes it that way, and the full text is on screen regardless); a
# cap keeps a runaway reply from turning into minutes of Piper audio.
MAX_SPEAK_CHARS = 600


class TranscribeRequest(BaseModel):
    audio_b64: str
    filename: str = "utterance.webm"
    # Also identify who is speaking from the same clip (voiceprint match against enrolled
    # people). Failures degrade to speaker=null, never a failed transcription.
    identify: bool = True


class EnrollRequest(BaseModel):
    """Two-stage voice enrollment, stateless across calls so the UI can drive it through BOTH
    CDG approval gates (voice-mcp-server.enroll_voiceprint and memory-mcp-server.store_voice
    each require human sign-off - biometric enrollment is deliberately double-gated):

    Stage 1: {person_id, audio_b64}                    -> pending_enroll + approval id
             {person_id, audio_b64, enroll_approval_id} -> embedding computed
                                                          -> pending_store + approval id + embedding
    Stage 2: {person_id, embedding, store_approval_id}  -> enrolled
    """

    person_id: str
    audio_b64: str = ""
    enroll_approval_id: str | None = None
    embedding: list[float] | None = None
    store_approval_id: str | None = None


class ChallengeVerifyRequest(BaseModel):
    audio_b64: str
    filename: str = "challenge.webm"


class WakeRequest(BaseModel):
    audio_b64: str
    filename: str = "wake.webm"


class SpeakRequest(BaseModel):
    text: str


class SetWakeWordRequest(BaseModel):
    wake_id: str
    # Set on a retry after the first call came back pending_approval and someone approved the
    # card - same continuation shape as EnrollRequest's stage-2 fields below.
    approval_request_id: str | None = None


class SetVoiceRequest(BaseModel):
    voice_id: str
    # Same two-stage continuation as SetWakeWordRequest above, for the same reason: this is a
    # household-wide setting, so an admin picking it is not the same event as a human approving it.
    approval_request_id: str | None = None


def _clip_for_speech(text: str, limit: int = MAX_SPEAK_CHARS) -> str:
    """Trim `text` to a speakable length, cutting on the last sentence boundary before `limit`
    when there is one (so speech ends on a whole sentence, not mid-word); otherwise hard-cap and
    mark the elision. The frontend still shows the full reply - only the audio is shortened."""
    text = text.strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    cut = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if cut >= limit // 2:
        return head[: cut + 1]
    return head.rstrip() + "…"


def build_voice_router(deps: SharedDeps) -> APIRouter:
    router = APIRouter()
    host = deps.host
    settings = deps.settings
    challenges = deps.challenges
    voice_limiter = deps.voice_limiter

    async def _identify_speaker(audio_b64: str, filename: str) -> dict | None:
        """Who is speaking? voiceprint embedding (voice-mcp-server) -> nearest enrolled match
        (memory-mcp-server) -> profile. Every failure path returns None ("unknown speaker",
        which maps to lowest access) - identification must never break the transcription it
        rides along with, and must never fail toward a false acceptance.
        """
        connected = host.mcp.connected_servers()
        if "voice-mcp-server" not in connected or "memory-mcp-server" not in connected:
            return None
        try:
            id_result = await _tool_json(
                deps, "voice-mcp-server", "identify_speaker", {"audio_base64": audio_b64},
                requested_by="tau-core-web-identity",
            )
            embedding = id_result["embedding"]
            matches = await _tool_json(
                deps, "memory-mcp-server", "match_voice", {"embedding": embedding, "top_k": 1},
                requested_by="tau-core-web-identity",
            )
            if isinstance(matches, dict):
                matches = [matches]
            if not matches:
                return None
            best = matches[0]
            if best["distance"] > settings.voice_match_max_distance:
                return None
            profile = await _tool_json(
                deps, "memory-mcp-server", "get_person_profile", {"person_id": best["person_id"]},
                requested_by="tau-core-web-identity",
            )
            access = (profile or {}).get("access_level") if isinstance(profile, dict) else None
            return {
                "person_id": best["person_id"],
                "distance": best["distance"],
                "access_level": access or "standard",
            }
        except Exception:  # noqa: BLE001 - unknown speaker, never a failed request
            logger.warning("Speaker identification failed; treating speaker as unknown", exc_info=True)
            return None

    @router.post("/api/voice/transcribe")
    async def voice_transcribe(
        body: TranscribeRequest,
        request: Request,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Local speech-to-text: browser MediaRecorder audio in, text out, via
        voice-mcp-server.transcribe -> the local faster-whisper service (infra/whisper).
        Goes through host.call_tool like everything else, so STT usage is audited. This is
        the local-first replacement for the browser SpeechRecognition path, whose Chrome
        implementation ships audio to Google (see frontend/README.md).
        """
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        _enforce_rate_limit(voice_limiter, _client_key(request))
        if len(body.audio_b64) > MAX_ATTACHMENT_B64_CHARS:
            raise HTTPException(status_code=413, detail="audio exceeds the 10MB limit")
        try:
            outcome = await host.call_tool(
                "voice-mcp-server",
                "transcribe",
                {"audio_base64": body.audio_b64, "filename": body.filename},
                requested_by="tau-core-web-voice",
            )
        except CoreDirectiveViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        except Exception as exc:  # noqa: BLE001 - sanitized (Phase 7 Tier 1 #9); logged below
            raise _sanitized_error(exc, 502, "Transcription failed.", "voice_transcribe") from exc

        if outcome.status is not ToolCallStatus.EXECUTED or not outcome.result:
            raise HTTPException(status_code=502, detail=f"Transcription did not run: {outcome.reason}")
        content = outcome.result.content
        text = content[0].text if content else ""
        if getattr(outcome.result, "isError", False):
            raise HTTPException(status_code=502, detail=f"Transcription failed: {text}")

        speaker = await _identify_speaker(body.audio_b64, body.filename) if body.identify else None
        return {"text": text, "speaker": speaker}

    @router.post("/api/voice/wake")
    async def voice_wake(
        body: WakeRequest,
        request: Request,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Local wake-word detection over a short rolling audio window, via
        voice-mcp-server.detect_wake_word -> openWakeWord (on-device, no cloud). The frontend
        polls this in local voice mode; on {detected: true} it fires the same invoke path as a
        double-tap. Read-only perception (CDG default-allow), so no approval - but see
        wake_word.py on the high-cadence audit-noise tradeoff.
        """
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        _enforce_rate_limit(voice_limiter, _client_key(request))
        if len(body.audio_b64) > MAX_ATTACHMENT_B64_CHARS:
            raise HTTPException(status_code=413, detail="audio exceeds the 10MB limit")
        try:
            detection = await _tool_json(
                deps,
                "voice-mcp-server",
                "detect_wake_word",
                {"audio_base64": body.audio_b64},
                requested_by="tau-core-web-wake",
            )
        except CoreDirectiveViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        except Exception as exc:  # noqa: BLE001 - sanitized (Phase 7 Tier 1 #9); logged below
            raise _sanitized_error(exc, 502, "Wake detection failed.", "voice_wake") from exc
        return detection

    @router.get("/api/voice/wake-words")
    async def list_wake_words_route(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
    ) -> list[dict]:
        """Wake words available to switch to (openWakeWord's bundled detectors plus this
        project's own trained "hey tau"), the currently active one flagged - backs the Settings
        picker (WakeWordPanel). Admin-gated like the rest of the settings surface (device list,
        activity feed) - this is read-only (voice-mcp-server.list_wake_words is default-allow),
        so the admin gate here is the only one, unlike the POST below.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        try:
            return await _tool_json(
                deps, "voice-mcp-server", "list_wake_words", {}, requested_by="tau-core-web-wake-words",
            )
        except Exception as exc:  # noqa: BLE001 - sanitized + logged
            raise _sanitized_error(exc, 502, "Could not list wake words.", "list_wake_words") from exc

    @router.post("/api/voice/wake-words")
    async def set_wake_word_route(
        body: SetWakeWordRequest,
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
    ) -> dict:
        """Switch the active local wake word. Double-gated on purpose: _require_admin below
        (who may even attempt this) AND the CDG's voice-set-wake-word-needs-approval rule
        (voice-mcp-server.set_wake_word requires_approval - the CDG has no caller-identity
        concept, so it gates this the same way whether a human clicked a button or Tau proposed
        it itself). An admin tapping SELECT is not the same thing as a human approving - this
        changes what every device in the household listens for - so the first call typically
        comes back pending_approval; the caller retries with approval_request_id once the card
        is approved, same continuation shape as /api/voice/enroll.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        try:
            outcome = await host.call_tool(
                "voice-mcp-server", "set_wake_word", {"wake_id": body.wake_id},
                approval_request_id=body.approval_request_id,
                requested_by="tau-core-web-wake-words",
            )
        except CoreDirectiveViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        except Exception as exc:  # noqa: BLE001 - sanitized + logged, e.g. an unknown wake_id
            raise _sanitized_error(exc, 502, "Could not set wake word.", "set_wake_word") from exc
        if outcome.status is not ToolCallStatus.EXECUTED:
            return {
                "status": "pending_approval" if outcome.status is ToolCallStatus.PENDING_APPROVAL else outcome.status.value,
                "reason": outcome.reason,
                "approval_request_id": outcome.approval_request_id,
            }
        content = outcome.result.content if outcome.result else []
        payload = json.loads(content[0].text) if content else {}
        return {"status": "ok", **payload}

    @router.get("/api/voice/voices")
    async def list_voices_route(
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
    ) -> list[dict]:
        """Speaking voices available to switch Tau to, the active one flagged and each marked with
        whether the TTS service actually has that model installed - backs the Settings picker
        (VoicePanel). Admin-gated like the wake-word listing above; read-only, so this gate is the
        only one, unlike the POST below.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        try:
            return await _tool_json(
                deps, "voice-mcp-server", "list_voices", {}, requested_by="tau-core-web-voices",
            )
        except Exception as exc:  # noqa: BLE001 - sanitized + logged
            raise _sanitized_error(exc, 502, "Could not list voices.", "list_voices") from exc

    @router.post("/api/voice/voices")
    async def set_voice_route(
        body: SetVoiceRequest,
        x_tau_voice_token: str | None = Header(default=None),
        x_tau_admin_service_token: str | None = Header(default=None),
    ) -> dict:
        """Switch the voice Tau speaks in. Double-gated exactly like the wake-word POST above:
        _require_admin decides who may attempt it, and the CDG's voice-set-tts-voice-needs-approval
        rule decides whether it happens. An admin tapping SELECT is not a human approving a change
        to how Tau sounds on every device, so the first call typically returns pending_approval and
        the caller retries with approval_request_id once the card is approved.
        """
        _require_admin(deps, x_tau_voice_token, x_tau_admin_service_token)
        try:
            outcome = await host.call_tool(
                "voice-mcp-server", "set_voice", {"voice_id": body.voice_id},
                approval_request_id=body.approval_request_id,
                requested_by="tau-core-web-voices",
            )
        except CoreDirectiveViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        except Exception as exc:  # noqa: BLE001 - sanitized + logged, e.g. an unknown voice_id
            raise _sanitized_error(exc, 502, "Could not set the voice.", "set_voice") from exc
        if outcome.status is not ToolCallStatus.EXECUTED:
            return {
                "status": "pending_approval" if outcome.status is ToolCallStatus.PENDING_APPROVAL else outcome.status.value,
                "reason": outcome.reason,
                "approval_request_id": outcome.approval_request_id,
            }
        content = outcome.result.content if outcome.result else []
        payload = json.loads(content[0].text) if content else {}
        return {"status": "ok", **payload}

    @router.post("/api/voice/speak")
    async def voice_speak(
        body: SpeakRequest,
        request: Request,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Neural TTS for a reply (Phase 13, voice-out): text in, base64 WAV out, via
        voice-mcp-server.speak -> Piper. The frontend calls this after a VOICE-initiated turn to
        read Tau's short answer aloud; typed turns stay silent. speak is a read-only/output tool
        (CDG default-allow), and the call is audited like any other. **Best-effort by contract:**
        a missing or broken TTS service returns 503, and the already-delivered text reply is
        unaffected - speaking is an enhancement, never a dependency of answering.
        """
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        _enforce_rate_limit(voice_limiter, _client_key(request))
        text = body.text.strip()
        if not text:
            raise HTTPException(status_code=400, detail="text must not be empty")
        try:
            result = await _tool_json(
                deps,
                "voice-mcp-server",
                "speak",
                {"text": _clip_for_speech(text)},
                requested_by="tau-core-web-speak",
            )
        except CoreDirectiveViolation as exc:
            raise HTTPException(status_code=403, detail=exc.reason) from exc
        except Exception as exc:  # noqa: BLE001 - TTS is best-effort; sanitized + logged, 503 not 502
            raise _sanitized_error(exc, 503, "Text-to-speech is unavailable.", "voice_speak") from exc
        if not isinstance(result, dict) or not result.get("audio_base64"):
            raise HTTPException(status_code=503, detail="Text-to-speech returned no audio.")
        return {"audio_base64": result["audio_base64"], "format": result.get("format", "wav")}

    @router.post("/api/voice/enroll")
    async def voice_enroll(
        body: EnrollRequest,
        request: Request,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Two-stage voiceprint enrollment - see EnrollRequest. Both stages are CDG
        approval-gated on purpose (biometric enrollment is exactly what the approval queue is
        for); this endpoint just orchestrates the embedding handoff between voice-mcp-server
        and memory-mcp-server around those gates.
        """
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        _enforce_rate_limit(voice_limiter, _client_key(request))
        person_id = body.person_id.strip().lower()
        if not person_id:
            raise HTTPException(status_code=400, detail="person_id must not be empty")

        # Stage 2: embedding already computed and approved for storage.
        if body.embedding is not None and body.store_approval_id:
            outcome = await host.call_tool(
                "memory-mcp-server", "store_voice",
                {"person_id": person_id, "embedding": body.embedding},
                approval_request_id=body.store_approval_id,
                requested_by="tablet-ui-enroll",
            )
            if outcome.status is ToolCallStatus.EXECUTED:
                return {"status": "enrolled", "person_id": person_id}
            return {
                "status": outcome.status.value,
                "reason": outcome.reason,
                "approval_request_id": outcome.approval_request_id,
            }

        # Stage 1: compute the embedding (approval-gated), then request storage approval.
        if len(body.audio_b64) > MAX_ATTACHMENT_B64_CHARS:
            raise HTTPException(status_code=413, detail="audio exceeds the 10MB limit")
        if not body.audio_b64:
            raise HTTPException(status_code=400, detail="audio_b64 is required for stage 1")

        outcome = await host.call_tool(
            "voice-mcp-server", "enroll_voiceprint",
            {"person_id": person_id, "audio_base64": body.audio_b64},
            approval_request_id=body.enroll_approval_id,
            requested_by="tablet-ui-enroll",
        )
        if outcome.status is not ToolCallStatus.EXECUTED:
            return {
                "status": "pending_enroll" if outcome.status is ToolCallStatus.PENDING_APPROVAL else outcome.status.value,
                "reason": outcome.reason,
                "approval_request_id": outcome.approval_request_id,
            }
        content = outcome.result.content if outcome.result else []
        payload = json.loads(content[0].text) if content else {}
        embedding = payload.get("embedding")
        if not embedding:
            raise HTTPException(status_code=502, detail="voice-mcp-server returned no embedding")

        store_outcome = await host.call_tool(
            "memory-mcp-server", "store_voice",
            {"person_id": person_id, "embedding": embedding},
            requested_by="tablet-ui-enroll",
        )
        if store_outcome.status is ToolCallStatus.EXECUTED:
            return {"status": "enrolled", "person_id": person_id}
        return {
            "status": "pending_store",
            "reason": store_outcome.reason,
            "approval_request_id": store_outcome.approval_request_id,
            "embedding": embedding,
        }

    @router.post("/api/voice/challenge")
    async def voice_challenge(
        request: Request,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Issue a random challenge phrase for replay-resistant identity verification. The
        speaker must say the phrase within its TTL; a recording can't predict it."""
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        _enforce_rate_limit(voice_limiter, _client_key(request))
        challenge_id, phrase = challenges.create()
        return {"challenge_id": challenge_id, "phrase": phrase}

    @router.post("/api/voice/challenge/{challenge_id}/verify")
    async def voice_challenge_verify(
        challenge_id: str,
        body: ChallengeVerifyRequest,
        request: Request,
        x_tau_device_id: str | None = Header(default=None),
        x_tau_device_token: str | None = Header(default=None),
    ) -> dict:
        """Verify a spoken challenge: the WORDS must match the phrase (transcription) AND the
        VOICE must match an enrolled person (voiceprint). Both, or no token."""
        _device_id(deps, x_tau_device_id, x_tau_device_token)
        _enforce_rate_limit(voice_limiter, _client_key(request))
        phrase = challenges.get_phrase(challenge_id)
        if phrase is None:
            raise HTTPException(status_code=404, detail="Unknown or expired challenge")

        try:
            heard = await _tool_json(
                deps,
                "voice-mcp-server", "transcribe",
                {"audio_base64": body.audio_b64, "filename": body.filename},
                requested_by="tau-core-web-identity",
            )
        except Exception as exc:  # noqa: BLE001 - sanitized (Phase 7 Tier 1 #9); logged below
            raise _sanitized_error(
                exc, 502, "Challenge transcription failed.", "voice_challenge_verify"
            ) from exc
        heard_text = heard if isinstance(heard, str) else json.dumps(heard)

        score = phrase_match_score(phrase, heard_text)
        if score < WORD_MATCH_THRESHOLD:
            return {"verified": False, "reason": f"phrase mismatch (matched {score:.0%} of words)"}

        speaker = await _identify_speaker(body.audio_b64, body.filename)
        if speaker is None:
            return {"verified": False, "reason": "voice did not match any enrolled person"}

        token = challenges.mark_verified(
            challenge_id, speaker["person_id"], speaker["access_level"]
        )
        if token is None:
            raise HTTPException(status_code=404, detail="Challenge expired during verification")
        return {"verified": True, "person_id": speaker["person_id"],
                "access_level": speaker["access_level"], "voice_token": token}

    return router
