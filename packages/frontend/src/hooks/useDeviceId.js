import { useCallback, useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'
import {
  getDeviceId,
  getDeviceName,
  setDeviceName,
  getDeviceToken,
  setDeviceToken,
  deviceHeaders,
} from '../utils/device'

/**
 * Owns this client's device identity (Phase 6.D): a stable localStorage id and a human-readable
 * name. Announces the device to the bridge once on mount (POST /api/devices/register) so it shows
 * up in the admin device list, and exposes a rename() the settings/admin UI can call. Registration
 * is best-effort - a failed announce never blocks the app; the next request's header still carries
 * the id, and touch() on the server records it anyway.
 *
 * Phase 27.A: also owns this device's bearer token, once one exists. There is no auto-fetch path
 * (see server.py's approve_device docstring) - an admin approves this device from the dashboard
 * and copies the token shown there; saveToken() is what a "paste it here" field on this device
 * calls to store it. Until TAU_REQUIRE_DEVICE_TOKEN is on, having no token changes nothing.
 */
export function useDeviceId() {
  const deviceId = getDeviceId()
  const [name, setName] = useState(getDeviceName())
  const [hasToken, setHasToken] = useState(() => Boolean(getDeviceToken()))

  const register = useCallback(async (newName) => {
    try {
      await fetch(`${getApiBase()}/api/devices/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...deviceHeaders() },
        body: JSON.stringify({ device_id: deviceId, name: newName || getDeviceName() }),
      })
    } catch {
      // best-effort; ignore
    }
  }, [deviceId])

  const rename = useCallback(
    (newName) => {
      setDeviceName(newName)
      setName(newName)
      register(newName)
    },
    [register],
  )

  useEffect(() => {
    register(getDeviceName())
  }, [register])

  const saveToken = useCallback((token) => {
    const trimmed = (token || '').trim()
    if (!trimmed) return
    setDeviceToken(trimmed)
    setHasToken(true)
  }, [])

  return { deviceId, name, rename, hasToken, saveToken }
}
