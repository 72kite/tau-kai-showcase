import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function SecurityPanel({ token }) {
  const [security, setSecurity] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api
      .securitySnapshot(token)
      .then(setSecurity)
      .catch((e) => setError(e.message))
  }, [token])

  return (
    <section className="panel">
      <h2>Security posture</h2>
      {error && <p className="panel-error">{error}</p>}
      {security && (
        <dl className="panel-dl">
          <dt>Voice approval required</dt>
          <dd>{String(security.require_voice_approval)}</dd>
          <dt>Voice match ceiling</dt>
          <dd>{security.voice_match_max_distance}</dd>
        </dl>
      )}
    </section>
  )
}
