import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

/**
 * Ported from packages/frontend/src/components/WakeWordPanel.jsx (Phase 38): same two-stage
 * approval flow (voice-mcp-server.set_wake_word is CDG-gated, so the first submit typically
 * comes back pending_approval), just talking to tau-admin-server's session auth instead of a
 * voice token.
 */
export default function WakeWordPanel({ token }) {
  const [options, setOptions] = useState([])
  const [error, setError] = useState(null)
  const [flow, setFlow] = useState(null) // { stage: 'pending' | 'error', wakeId, approvalId, note }

  const refresh = useCallback(async () => {
    try {
      setOptions(await api.listWakeWords(token))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  useEffect(() => {
    refresh()
  }, [refresh])

  const submit = useCallback(
    async (wakeId, approvalId) => {
      try {
        const body = await api.setWakeWord(token, wakeId, approvalId)
        if (body.status === 'ok') {
          setFlow(null)
          await refresh()
        } else if (body.status === 'pending_approval') {
          setFlow({
            stage: 'pending',
            wakeId,
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

  return (
    <section className="panel">
      <h2>Wake word</h2>
      {error && <p className="panel-error">{error}</p>}
      <ul className="wakeword-list">
        {options.map((opt) => (
          <li key={opt.id} className={opt.current ? 'current' : ''}>
            <span>{opt.label}</span>
            {opt.current ? (
              <span className="tag">Active</span>
            ) : (
              <button
                type="button"
                onClick={() => submit(opt.id)}
                disabled={flow?.stage === 'pending' && flow.wakeId !== opt.id}
              >
                Select
              </button>
            )}
          </li>
        ))}
      </ul>
      {flow?.stage === 'pending' && (
        <div className="panel-note">
          {flow.note}
          <button type="button" onClick={() => submit(flow.wakeId, flow.approvalId)}>
            Continue
          </button>
        </div>
      )}
      {flow?.stage === 'error' && <p className="panel-error">{flow.note}</p>}
    </section>
  )
}
