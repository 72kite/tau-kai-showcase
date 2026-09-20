import { useCallback, useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'
import { recordClip } from '../utils/recordClip'
import { authHeaders } from '../utils/adminAuth'

/**
 * Polls tau-core's pending-approval queue (the generic CDG sign-off primitive from Phase 1,
 * project-tau-plan.md section 4) and exposes approve/deny actions that POST the human's
 * decision back through the bridge - see tau_core.web.server.
 *
 * Voice-verified approvals: when the bridge is running with TAU_REQUIRE_VOICE_APPROVAL, a
 * decision without a verified voice token comes back 428. This hook then fetches a challenge
 * phrase and exposes it via `challenge`; the UI shows the phrase, the user taps SPEAK and
 * reads it aloud (recordClip), and on successful verification (words match + voiceprint
 * matches an enrolled person) the original decision is retried with the single-use token.
 * A replayed recording can't predict the phrase; another person reading it fails the
 * voiceprint - see tau_core/web/identity.py.
 */
export function useApprovals(pollIntervalMs = 2000) {
  const [approvals, setApprovals] = useState([])
  const [error, setError] = useState(null)
  const [challenge, setChallenge] = useState(null)
  // { requestId, decision, challengeId, phrase, status: 'ready'|'recording'|'verifying'|'failed', note }

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/approvals`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setApprovals(await res.json())
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, pollIntervalMs)
    return () => clearInterval(id)
  }, [refresh, pollIntervalMs])

  const postDecision = useCallback(async (requestId, decision, voiceToken) => {
    return fetch(`${getApiBase()}/api/approvals/${requestId}/${decision}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders(voiceToken) },
      body: JSON.stringify({ decided_by: 'tablet-ui' }),
    })
  }, [])

  const decide = useCallback(
    async (requestId, decision) => {
      try {
        const res = await postDecision(requestId, decision, null)
        if (res.status === 428) {
          // Voice verification required: fetch a challenge phrase and hand it to the UI.
          const ch = await (await fetch(`${getApiBase()}/api/voice/challenge`, { method: 'POST' })).json()
          setChallenge({
            requestId,
            decision,
            challengeId: ch.challenge_id,
            phrase: ch.phrase,
            status: 'ready',
            note: 'Speak the phrase to verify it is really you',
          })
        }
      } finally {
        refresh()
      }
    },
    [postDecision, refresh]
  )

  const speakChallenge = useCallback(async () => {
    if (!challenge) return
    try {
      setChallenge((c) => ({ ...c, status: 'recording', note: 'Recording - read the phrase aloud…' }))
      const clip = await recordClip(4)
      setChallenge((c) => ({ ...c, status: 'verifying', note: 'Verifying words + voiceprint…' }))
      const res = await fetch(`${getApiBase()}/api/voice/challenge/${challenge.challengeId}/verify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ audio_b64: clip.data_b64, filename: clip.filename }),
      })
      const body = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
      if (!body.verified) {
        setChallenge((c) => ({ ...c, status: 'failed', note: body.reason || 'verification failed' }))
        return
      }
      await postDecision(challenge.requestId, challenge.decision, body.voice_token)
      setChallenge(null)
    } catch (e) {
      setChallenge((c) => (c ? { ...c, status: 'failed', note: e.message } : null))
    } finally {
      refresh()
    }
  }, [challenge, postDecision, refresh])

  const cancelChallenge = useCallback(() => setChallenge(null), [])

  return {
    approvals,
    error,
    challenge,
    speakChallenge,
    cancelChallenge,
    approve: (id) => decide(id, 'approve'),
    deny: (id) => decide(id, 'deny'),
  }
}
