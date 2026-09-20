import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

const POLL_MS = 4000

function timeOnly(ts) {
  return (ts || '').slice(11, 19) || '—'
}

/**
 * The cross-device conversation log (Phase 6.D, admin-only) - what every kiosk/device has said
 * to and heard from Tau, in one place. The last panel ported from the kiosk's old AdminDashboard;
 * everything else moved over in Phase 40's first pass. Not durable across a ui-bridge-mcp-server
 * restart (Phase 14 gives it its own volume, but that's a separate concern from this panel).
 */
export default function UnifiedLogPanel({ token }) {
  const [entries, setEntries] = useState([])
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const body = await api.unifiedTranscript(token)
      setEntries(body.history || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, POLL_MS)
    return () => clearInterval(id)
  }, [refresh])

  return (
    <section className="panel">
      <h2>Unified log ({entries.length})</h2>
      {error && <p className="panel-error">{error}</p>}
      <ul className="log-list">
        {entries.slice(-60).map((e, i) => (
          <li key={`${e.timestamp}-${i}`}>
            <span className="log-time">{timeOnly(e.timestamp)}</span>
            <span className="log-speaker">{e.speaker}</span>
            {e.device_id && (
              <span className="log-device" title={e.device_id}>
                {e.device_id.slice(0, 6)}
              </span>
            )}
            <span className="log-text">{e.text}</span>
          </li>
        ))}
        {entries.length === 0 && !error && <li>No conversation yet.</li>}
      </ul>
    </section>
  )
}
