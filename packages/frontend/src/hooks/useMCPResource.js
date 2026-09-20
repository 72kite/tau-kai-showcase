import { useCallback, useEffect, useState } from 'react'

// tau-core's HTTP bridge (tau_core.web.server) - see project-tau-plan.md Phase 3. Runs as a
// separate process from the Vite dev server, so it needs its own host:port; on a LAN kiosk
// tablet this resolves to whatever machine tau-core is running on.
// `??`, not `||`: an explicitly-empty VITE_TAU_API_BASE (the Docker build default) means
// "same origin" - fetches go to relative "/api/...", which the frontend's nginx proxies to
// tau-core (one origin => works over https behind a reverse proxy / Tailscale, no CORS, no
// mixed content). An UNSET var (e.g. `npm run dev`) still falls back to the direct :8000 origin,
// so two-origin local dev keeps working.
function computeDefaultApiBase() {
  return import.meta.env.VITE_TAU_API_BASE ?? `http://${window.location.hostname}:8000`
}

// Phase 27.D: the Tauri desktop shell has no same-origin tau-core and no build-time env var (one
// install, any LAN host), so it needs a *runtime* override - a plain constant computed once at
// module load (the kiosk's whole model) can't represent that. getApiBase() is read fresh on every
// call instead, checking a user-set override before falling back to the kiosk's original
// same-origin/env-var logic - so the kiosk build (which never calls setApiBase) sees byte-for-byte
// the same value it always did.
const OVERRIDE_STORAGE_KEY = 'tau-api-base-override'

export function getApiBase() {
  return localStorage.getItem(OVERRIDE_STORAGE_KEY) || computeDefaultApiBase()
}

export function setApiBase(url) {
  localStorage.setItem(OVERRIDE_STORAGE_KEY, url || '')
}

// Gates the Tauri desktop shell's first-run "connect to tau-core" screen (App.jsx) - the kiosk
// build never checks this, since it never needs an override in the first place.
export function hasApiBaseOverride() {
  return Boolean(localStorage.getItem(OVERRIDE_STORAGE_KEY))
}

/**
 * Polls one MCP Resource through tau-core's bridge and returns its latest value.
 *
 * Deliberately polling rather than WebSocket: this runs on older iPads over home Wi-Fi that
 * drops sockets far more often than it drops a single short-lived GET. Polling means a bad
 * request just gets retried on the next tick with no reconnect state machine to get stuck in.
 * Each poll waits for the previous one to finish before scheduling the next (rather than a
 * fixed setInterval), so slow/stalled requests don't pile up concurrently on a weak connection.
 */
export function useMCPResource(uri, { server = 'ui-bridge-mcp-server', pollIntervalMs = 1000 } = {}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchOnce = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/resources/${server}/${encodeURIComponent(uri)}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
      setError(null)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [uri, server])

  useEffect(() => {
    if (!uri) return undefined
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
  }, [uri, fetchOnce, pollIntervalMs])

  return { data, loading, error }
}
