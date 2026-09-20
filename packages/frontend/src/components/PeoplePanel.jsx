import React, { useCallback, useState } from 'react'
import { getApiBase } from '../hooks/useMCPResource'
import { recordClip } from '../utils/recordClip'
import './Panel.css'

const SAMPLE_SECONDS = 5

/**
 * Known-people roster (memory-mcp-server profiles) plus voiceprint enrollment. Enrollment is
 * deliberately double-gated by the CDG (voice-mcp-server.enroll_voiceprint AND
 * memory-mcp-server.store_voice each need human approval) - this panel walks the user through
 * both gates: record a sample, approve the card that appears, tap CONTINUE, approve the
 * second card, tap CONTINUE again. Clunkier than one tap, and that's the point: enrolling a
 * biometric identity should never be one accidental tap.
 */
export default function PeoplePanel({ people = [], error }) {
  const [name, setName] = useState('')
  const [flow, setFlow] = useState(null) // { stage, personId, note, ...continuation fields }

  const post = useCallback(async (body) => {
    const res = await fetch(`${getApiBase()}/api/voice/enroll`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const parsed = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(parsed.detail || `HTTP ${res.status}`)
    return parsed
  }, [])

  const advance = useCallback(
    (personId, result, continuation) => {
      if (result.status === 'enrolled') {
        setFlow({ stage: 'done', personId, note: `${personId} enrolled - voice recognized from now on` })
      } else if (result.status === 'pending_enroll') {
        setFlow({
          stage: 'await_enroll_approval',
          personId,
          note: 'Approve the enrollment card (1 of 2), then tap CONTINUE',
          ...continuation,
          enrollApprovalId: result.approval_request_id,
        })
      } else if (result.status === 'pending_store') {
        setFlow({
          stage: 'await_store_approval',
          personId,
          note: 'Approve the storage card (2 of 2), then tap CONTINUE',
          embedding: result.embedding,
          storeApprovalId: result.approval_request_id,
        })
      } else {
        setFlow({ stage: 'error', personId, note: `${result.status}: ${result.reason || 'unexpected'}` })
      }
    },
    []
  )

  const startEnrollment = useCallback(async () => {
    const personId = name.trim().toLowerCase()
    if (!personId) return
    try {
      setFlow({ stage: 'recording', personId, note: `Recording ${SAMPLE_SECONDS}s - speak naturally…` })
      const clip = await recordClip(SAMPLE_SECONDS)
      setFlow({ stage: 'submitting', personId, note: 'Computing voiceprint…' })
      const result = await post({ person_id: personId, audio_b64: clip.data_b64 })
      advance(personId, result, { audioB64: clip.data_b64 })
    } catch (e) {
      setFlow({ stage: 'error', personId, note: e.message })
    }
  }, [name, post, advance])

  const continueEnrollment = useCallback(async () => {
    if (!flow) return
    try {
      if (flow.stage === 'await_enroll_approval') {
        const result = await post({
          person_id: flow.personId,
          audio_b64: flow.audioB64,
          enroll_approval_id: flow.enrollApprovalId,
        })
        advance(flow.personId, result, { audioB64: flow.audioB64 })
      } else if (flow.stage === 'await_store_approval') {
        const result = await post({
          person_id: flow.personId,
          embedding: flow.embedding,
          store_approval_id: flow.storeApprovalId,
        })
        advance(flow.personId, result, {})
      }
    } catch (e) {
      setFlow({ stage: 'error', personId: flow.personId, note: e.message })
    }
  }, [flow, post, advance])

  const showContinue = flow && (flow.stage === 'await_enroll_approval' || flow.stage === 'await_store_approval')

  return (
    <div className="panel people-panel">
      <div className="panel-title">KNOWN PEOPLE</div>
      <div className="panel-content">
        {error && (
          <div className="stat-line">
            <span className="stat-label">STATUS:</span>
            <span className="stat-value">UNAVAILABLE</span>
          </div>
        )}
        {!error && people.length === 0 && (
          <div className="stat-line">
            <span className="stat-label">ENROLLED:</span>
            <span className="stat-value">NONE</span>
          </div>
        )}
        {people.map((person) => (
          <div key={person.person_id} className="stat-line">
            <span className="stat-label">{person.person_id}</span>
            <span className="stat-value">
              {person.access_level.toUpperCase()} · {person.face_count}F/{person.voice_count}V
            </span>
          </div>
        ))}

        <div className="enroll-form">
          <input
            type="text"
            className="memory-search-input"
            placeholder="Name to enroll (new or existing)…"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <button
            type="button"
            className="memory-search-submit"
            onClick={startEnrollment}
            disabled={flow?.stage === 'recording' || flow?.stage === 'submitting'}
          >
            {flow?.stage === 'recording' ? 'RECORDING…' : `ENROLL VOICE (${SAMPLE_SECONDS}s)`}
          </button>
        </div>

        {flow && <div className="panel-note">{flow.note}</div>}
        {showContinue && (
          <button type="button" className="memory-search-submit enroll-continue" onClick={continueEnrollment}>
            CONTINUE ENROLLMENT
          </button>
        )}
      </div>
    </div>
  )
}
