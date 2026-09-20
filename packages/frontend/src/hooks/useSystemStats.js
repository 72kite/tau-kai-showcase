import { useCallback, useEffect, useState } from 'react'
import { isTauri } from '../utils/tauri'

/**
 * Polls tau-desktop's native get_system_stats command (src-tauri/src/system.rs) - CPU/RAM/disk of
 * the machine the CLIENT runs on, not tau-core's server. No web API exposes this, so it's Tauri-
 * only; the kiosk build never calls invoke() at all (isTauri() short-circuits before the dynamic
 * import even resolves, matching pairing.js's code-splitting reasoning).
 */
export function useSystemStats(pollIntervalMs = 5000) {
  const [stats, setStats] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    if (!isTauri()) return
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      setStats(await invoke('get_system_stats'))
      setError(null)
    } catch (e) {
      setError(e?.message || String(e))
    }
  }, [])

  useEffect(() => {
    if (!isTauri()) return undefined
    refresh()
    const id = setInterval(refresh, pollIntervalMs)
    return () => clearInterval(id)
  }, [refresh, pollIntervalMs])

  return { stats, error }
}
