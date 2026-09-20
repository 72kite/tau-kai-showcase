import React, { useEffect, useState } from 'react'
import { getApiBase } from '../hooks/useMCPResource'
import { authHeaders } from '../utils/adminAuth'
import './Panel.css'

const POLL_MS = 4000

const OUTCOME_LABELS = {
  executed: 'OK',
  pending: 'PENDING',
  denied: 'DENIED',
  clarify: 'CLARIFY',
}

/**
 * Live view of tau-core's audit trail (/api/activity - the same JSON events the stderr audit
 * stream carries): every tool call with its CDG effect and outcome, newest first. This is the
 * project's auditability pillar made visible on the kiosk - including calls that were blocked
 * or queued, which are exactly the ones a human most wants to notice.
 *
 * Admin-gated (Phase 16) - `voiceToken` comes from useAdmin, same token the rest of the admin
 * dashboard already verified; this panel does not run its own challenge flow.
 */
export default function ActivityPanel({ voiceToken = null }) {
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)
  const [needsAdmin, setNeedsAdmin] = useState(false)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/activity?limit=50`, { headers: authHeaders(voiceToken) })
        if (res.status === 428 || res.status === 403) {
          if (!cancelled) setNeedsAdmin(true)
          return
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const body = await res.json()
        if (!cancelled) {
          setEvents(body)
          setNeedsAdmin(false)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(e.message)
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [voiceToken])

  return (
    <div className="panel activity-panel">
      <div className="panel-title">ACTIVITY</div>
      <div className="panel-content">
        {error && <div className="panel-note">activity feed unavailable: {error}</div>}
        {needsAdmin && <div className="panel-note">Admin verification required to view the activity feed.</div>}
        {!error && !needsAdmin && events.length === 0 && <div className="panel-note">No tool calls yet.</div>}
        {events.map((event, i) => (
          <div
            key={`${event.timestamp}-${i}`}
            className={`activity-row outcome-${event.outcome || 'unknown'}`}
            title={event.reason || ''}
          >
            <span className="activity-time">
              {(event.timestamp || '').slice(11, 19) || '—'}
            </span>
            <span className="activity-call">
              {event.server}.{event.tool}
            </span>
            <span className={`activity-outcome outcome-${event.outcome || 'unknown'}`}>
              {OUTCOME_LABELS[event.outcome] || event.outcome || '?'}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
