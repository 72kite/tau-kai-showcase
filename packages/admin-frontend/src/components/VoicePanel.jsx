import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

/**
 * Picks the voice Tau speaks in. Same shape as WakeWordPanel next door, and for the same reason:
 * voice-mcp-server.set_voice is CDG-gated, so the first submit typically comes back
 * pending_approval and the caller retries with the approval id once someone approves the card.
 *
 * The one thing this panel has that the wake-word one doesn't is `available`. wyoming-piper only
 * downloads the voice named in its own --voice flag, so a voice the seed step never fetched will
 * still speak - just in the default voice, not the one named. Showing that up front is better than
 * letting someone pick a voice and quietly get a different one.
 */
export default function VoicePanel({ token }) {
  const [options, setOptions] = useState([])
  const [error, setError] = useState(null)
  const [flow, setFlow] = useState(null) // { stage: 'pending' | 'error', voiceId, approvalId, note }

  const refresh = useCallback(async () => {
    try {
      setOptions(await api.listVoices(token))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  useEffect(() => {
    refresh()
  }, [refresh])

  const submit = useCallback(
    async (voiceId, approvalId) => {
      try {
        const body = await api.setVoice(token, voiceId, approvalId)
        if (body.status === 'ok') {
          setFlow(null)
          await refresh()
        } else if (body.status === 'pending_approval') {
          setFlow({
            stage: 'pending',
            voiceId,
            approvalId: body.approval_request_id,
            note: 'Approve the resulting request, then Continue',
          })
        } else {
          setFlow({ stage: 'error', note: `${body.status}: ${body.reason || 'unexpected'}` })
        }
      } catch (e) {
        setFlow({ stage: 'error', note: e.message })
      }
    },
    [token, refresh]
  )

  const anyMissing = options.some((opt) => opt.available === false)

  return (
    <section className="panel">
      <h2>Voice</h2>
      {error && <p className="panel-error">{error}</p>}
      <ul className="wakeword-list">
        {options.map((opt) => (
          <li key={opt.id} className={opt.current ? 'current' : ''}>
            <span>{opt.label}</span>
            {opt.current ? (
              <span className="tag">Active</span>
            ) : opt.available === false ? (
              <span className="tag">Not downloaded</span>
            ) : (
              <button
                type="button"
                onClick={() => submit(opt.id)}
                disabled={flow?.stage === 'pending' && flow.voiceId !== opt.id}
              >
                Select
              </button>
            )}
          </li>
        ))}
      </ul>
      {anyMissing && (
        <p className="panel-note">
          A voice marked Not downloaded has no model on the speech service yet. Restart the stack to
          run the voice seed step, which fetches them.
        </p>
      )}
      {flow?.stage === 'pending' && (
        <div className="panel-note">
          {flow.note}
          <button type="button" onClick={() => submit(flow.voiceId, flow.approvalId)}>
            Continue
          </button>
        </div>
      )}
      {flow?.stage === 'error' && <p className="panel-error">{flow.note}</p>}
    </section>
  )
}
