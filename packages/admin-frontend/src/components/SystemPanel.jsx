import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function SystemPanel({ token }) {
  const [system, setSystem] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    api
      .systemSnapshot(token)
      .then(setSystem)
      .catch((e) => setError(e.message))
  }, [token])

  return (
    <section className="panel">
      <h2>System</h2>
      {error && <p className="panel-error">{error}</p>}
      {system && (
        <dl className="panel-dl">
          <dt>Model</dt>
          <dd>{system.current_model}</dd>
          <dt>CPU cores</dt>
          <dd>{system.hardware?.cpu_cores}</dd>
          <dt>GPU VRAM (MB)</dt>
          <dd>{system.hardware?.gpu_vram_mb}</dd>
        </dl>
      )}
    </section>
  )
}
