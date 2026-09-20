import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

/**
 * Simpler than the kiosk's DraftReviewPanel/useDrafts.js on purpose: that hook polls
 * /api/approvals directly to auto-retry a promotion once a human approves it elsewhere. This
 * panel doesn't have that background poll (v1) - after approving the resulting request, click
 * Continue here to re-issue the promotion, same two-stage shape as WakeWordPanel.
 */
export default function DraftsPanel({ token }) {
  const [drafts, setDrafts] = useState([])
  const [error, setError] = useState(null)
  const [pending, setPending] = useState({}) // nodeId -> { approvalId, note }

  const refresh = useCallback(async () => {
    try {
      // tau-core's /api/drafts (proxied as-is) returns a bare array of draft nodes, not
      // {drafts: [...]} - see useDrafts.js on the kiosk side for the same shape.
      setDrafts((await api.listDrafts(token)) || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  useEffect(() => {
    refresh()
  }, [refresh])

  const promote = async (nodeId, approvalId) => {
    try {
      const body = await api.promoteDraft(token, nodeId, approvalId)
      if (body.status === 'pending_approval') {
        setPending((m) => ({
          ...m,
          [nodeId]: { approvalId: body.approval_request_id, note: 'Approve, then Continue' },
        }))
      } else {
        setPending((m) => {
          const next = { ...m }
          delete next[nodeId]
          return next
        })
        refresh()
      }
    } catch (e) {
      setPending((m) => ({ ...m, [nodeId]: { note: e.message } }))
    }
  }

  const discard = async (nodeId) => {
    await api.discardDraft(token, nodeId)
    refresh()
  }

  return (
    <section className="panel">
      <h2>Draft memory review</h2>
      {error && <p className="panel-error">{error}</p>}
      <ul className="drafts-list">
        {drafts.map((d) => (
          <li key={d.id}>
            <p className="draft-title">{d.title}</p>
            <p>{d.content}</p>
            <p className="draft-owner">about: {d.owner || 'household (shared)'}</p>
            {pending[d.id] ? (
              <div className="panel-note">
                {pending[d.id].note}
                {pending[d.id].approvalId && (
                  <button type="button" onClick={() => promote(d.id, pending[d.id].approvalId)}>
                    Continue
                  </button>
                )}
              </div>
            ) : (
              <div className="panel-actions">
                <button type="button" onClick={() => promote(d.id)}>
                  Promote
                </button>
                <button type="button" onClick={() => discard(d.id)}>
                  Discard
                </button>
              </div>
            )}
          </li>
        ))}
        {drafts.length === 0 && <li>Nothing to review.</li>}
      </ul>
    </section>
  )
}
