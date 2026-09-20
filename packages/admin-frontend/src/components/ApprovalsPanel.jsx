import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

const POLL_MS = 5000

function timeOnly(ts) {
  return (ts || '').slice(11, 19) || '—'
}

/**
 * Pending CDG-gated actions (tau-core's PendingActionQueue). Before this panel, the only place to
 * actually decide one was the kiosk's ApprovalQueue overlay - an admin working from this panel
 * could see (via WakeWordPanel/DraftsPanel/UserProfilesPanel's own "approve, then Continue" note)
 * that something was blocked on an approval, but not act on it without leaving the page. Approving
 * here does not itself finish those flows - the admin still clicks that panel's own Continue - but
 * it removes the need to find a kiosk to get there.
 */
export default function ApprovalsPanel({ token }) {
  const [approvals, setApprovals] = useState([])
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  const refresh = useCallback(async () => {
    try {
      setApprovals(await api.listApprovals(token))
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

  const decide = async (id, action) => {
    setBusyId(id)
    try {
      if (action === 'approve') {
        await api.approveAction(token, id)
      } else {
        await api.denyAction(token, id)
      }
      await refresh()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className={`panel panel-approvals${approvals.length > 0 ? ' has-pending' : ''}`}>
      <h2>
        Pending approvals
        {approvals.length > 0 && <span className="badge-count">{approvals.length}</span>}
      </h2>
      {error && <p className="panel-error">{error}</p>}
      <ul className="approvals-list">
        {approvals.map((a) => (
          <li key={a.id} className="approval-card">
            <div className="approval-card-head">
              <span className="approval-tool">{a.tool}</span>
              <span className="approval-server">@{a.server}</span>
              <span className="approval-time">{timeOnly(a.requested_at)}</span>
            </div>
            {a.reason && <p className="approval-reason">{a.reason}</p>}
            {a.arguments && Object.keys(a.arguments).length > 0 && (
              <dl className="panel-dl approval-args">
                {Object.entries(a.arguments).map(([key, value]) => (
                  <React.Fragment key={key}>
                    <dt>{key}</dt>
                    <dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
                  </React.Fragment>
                ))}
              </dl>
            )}
            <div className="approval-card-foot">
              <span className="approval-requester">requested by {a.requested_by}</span>
              <div className="panel-actions">
                <button
                  type="button"
                  className="button-primary"
                  disabled={busyId === a.id}
                  onClick={() => decide(a.id, 'approve')}
                >
                  Approve
                </button>
                <button
                  type="button"
                  className="button-danger"
                  disabled={busyId === a.id}
                  onClick={() => decide(a.id, 'deny')}
                >
                  Deny
                </button>
              </div>
            </div>
          </li>
        ))}
        {approvals.length === 0 && !error && (
          <li className="approvals-empty">Nothing pending.</li>
        )}
      </ul>
    </section>
  )
}
