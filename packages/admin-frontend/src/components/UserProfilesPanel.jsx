import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

const ACCESS_LEVELS = ['unknown', 'kid', 'standard', 'admin']

/**
 * People roster + access-level editing in one panel - the kiosk splits this across PeoplePanel
 * (a read-only roster reused from the non-admin SystemDrawer) and UserProfilesPanel (the editor).
 * admin-frontend has no non-admin context to share a component with, so there's no reason to
 * keep them separate here.
 */
export default function UserProfilesPanel({ token }) {
  const [people, setPeople] = useState([])
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState({}) // personId -> selected (not yet submitted) level
  const [pending, setPending] = useState({}) // personId -> { approvalId, note }

  const refresh = useCallback(async () => {
    try {
      const body = await api.peopleSnapshot(token)
      setPeople(body.people || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  useEffect(() => {
    refresh()
  }, [refresh])

  const submit = async (personId, accessLevel, approvalId) => {
    try {
      const body = await api.setAccessLevel(token, personId, accessLevel, approvalId)
      if (body.status === 'pending_approval') {
        setPending((m) => ({
          ...m,
          [personId]: { approvalId: body.approval_request_id, accessLevel, note: 'Approve, then Continue' },
        }))
      } else {
        setPending((m) => {
          const next = { ...m }
          delete next[personId]
          return next
        })
        refresh()
      }
    } catch (e) {
      setPending((m) => ({ ...m, [personId]: { note: e.message } }))
    }
  }

  return (
    <section className="panel">
      <h2>People ({people.length})</h2>
      {error && <p className="panel-error">{error}</p>}
      <table className="panel-table">
        <thead>
          <tr>
            <th>Person</th>
            <th>Enrolled</th>
            <th>Access level</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {people.map((p) => {
            const p2 = pending[p.person_id]
            const level = draft[p.person_id] ?? p.access_level ?? 'unknown'
            return (
              <tr key={p.person_id}>
                <td>{p.person_id}</td>
                <td>
                  {p.face_count ?? 0} face / {p.voice_count ?? 0} voice
                </td>
                <td>
                  <select
                    value={level}
                    onChange={(e) => setDraft((d) => ({ ...d, [p.person_id]: e.target.value }))}
                    disabled={Boolean(p2)}
                  >
                    {ACCESS_LEVELS.map((lvl) => (
                      <option key={lvl} value={lvl}>
                        {lvl}
                      </option>
                    ))}
                  </select>
                </td>
                <td className="panel-actions">
                  {p2 ? (
                    p2.approvalId ? (
                      <button type="button" onClick={() => submit(p.person_id, p2.accessLevel, p2.approvalId)}>
                        Continue
                      </button>
                    ) : (
                      <span className="panel-note">{p2.note}</span>
                    )
                  ) : (
                    <button
                      type="button"
                      disabled={level === (p.access_level ?? 'unknown')}
                      onClick={() => submit(p.person_id, level)}
                    >
                      Set
                    </button>
                  )}
                  {p2 && p2.approvalId && <span className="panel-note">{p2.note}</span>}
                </td>
              </tr>
            )
          })}
          {people.length === 0 && (
            <tr>
              <td colSpan={4}>No enrolled people yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
