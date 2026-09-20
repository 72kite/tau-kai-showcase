import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'

export default function DevicesPanel({ token }) {
  const [devices, setDevices] = useState([])
  const [error, setError] = useState(null)
  const [revealedToken, setRevealedToken] = useState(null) // { deviceId, token } - shown once

  const refresh = useCallback(async () => {
    try {
      setDevices(await api.listDevices(token))
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [token])

  useEffect(() => {
    refresh()
  }, [refresh])

  const approve = async (deviceId) => {
    const body = await api.approveDevice(token, deviceId)
    setRevealedToken({ deviceId, token: body.token })
    refresh()
  }
  const block = async (deviceId) => {
    await api.blockDevice(token, deviceId)
    refresh()
  }
  const unblock = async (deviceId) => {
    await api.unblockDevice(token, deviceId)
    refresh()
  }

  return (
    <section className="panel">
      <h2>Devices</h2>
      {error && <p className="panel-error">{error}</p>}
      {revealedToken && (
        <div className="panel-note">
          Bearer token for <strong>{revealedToken.deviceId}</strong> (shown once - copy it onto
          that device now):
          <code className="reveal-token">{revealedToken.token}</code>
          <button type="button" onClick={() => setRevealedToken(null)}>
            Done
          </button>
        </div>
      )}
      <table className="panel-table">
        <thead>
          <tr>
            <th>Device</th>
            <th>Status</th>
            <th>Last seen</th>
            <th>Blocked</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {devices.map((d) => (
            <tr key={d.device_id}>
              <td>{d.name || d.device_id}</td>
              <td>{d.status}</td>
              <td>{d.last_seen}</td>
              <td>{d.blocked ? 'yes' : 'no'}</td>
              <td className="panel-actions">
                {d.status === 'pending' && (
                  <button type="button" onClick={() => approve(d.device_id)}>
                    Approve
                  </button>
                )}
                {d.blocked ? (
                  <button type="button" onClick={() => unblock(d.device_id)}>
                    Unblock
                  </button>
                ) : (
                  <button type="button" onClick={() => block(d.device_id)}>
                    Block
                  </button>
                )}
              </td>
            </tr>
          ))}
          {devices.length === 0 && (
            <tr>
              <td colSpan={5}>No devices seen yet.</td>
            </tr>
          )}
        </tbody>
      </table>
    </section>
  )
}
