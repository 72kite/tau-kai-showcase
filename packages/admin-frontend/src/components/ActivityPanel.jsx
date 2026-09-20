import React, { useEffect, useState } from 'react'
import { api } from '../api'

const OUTCOME_LABELS = {
  executed: 'OK',
  pending: 'PENDING',
  denied: 'DENIED',
  clarify: 'CLARIFY',
}

const POLL_MS = 4000

export default function ActivityPanel({ token }) {
  const [events, setEvents] = useState([])
  const [error, setError] = useState(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const body = await api.activityFeed(token, 50)
        if (!cancelled) {
          setEvents(body)
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
  }, [token])

  return (
    <section className="panel">
      <h2>Activity</h2>
      {error && <p className="panel-error">{error}</p>}
      <ul className="activity-list">
        {events.map((event, i) => (
          <li key={`${event.timestamp}-${i}`} className={`outcome-${event.outcome || 'unknown'}`}>
            <span className="activity-time">{(event.timestamp || '').slice(11, 19) || '—'}</span>
            <span className="activity-call">
              {event.server}.{event.tool}
            </span>
            <span className="activity-outcome">{OUTCOME_LABELS[event.outcome] || event.outcome || '?'}</span>
          </li>
        ))}
        {events.length === 0 && !error && <li>No tool calls yet.</li>}
      </ul>
    </section>
  )
}
