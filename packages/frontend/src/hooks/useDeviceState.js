import { useCallback, useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'
import { deviceHeaders } from '../utils/device'

/**
 * Polls the bridge's device-scoped /api/state (Phase 6.D). Same shape as ui-bridge's ui://state,
 * but the transcript is ONLY this device's own turns - the server filters by the X-Tau-Device-Id
 * header this hook sends. This replaces a direct poll of ui://state for the conversation view,
 * which leaked every device's chat to every client.
 *
 * Deliberately polling (not WebSocket) and awaiting each tick before scheduling the next, for
 * the same flaky-tablet-Wi-Fi reasons as useMCPResource.
 */
export function useDeviceState({ pollIntervalMs = 1000 } = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchOnce = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/state`, { headers: deviceHeaders() })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    let cancelled = false
    let timer
    const tick = async () => {
      if (cancelled) return
      await fetchOnce()
      if (!cancelled) timer = setTimeout(tick, pollIntervalMs)
    }
    tick()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [fetchOnce, pollIntervalMs])

  return { data, loading, error }
}
