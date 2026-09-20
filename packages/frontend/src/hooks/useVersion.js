import { useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'

// Baked in at build time by Vite's `define` (see vite.config.js): the bundle is static, so it can
// only know which build it *is*, never which build is current.
const FRONTEND_VERSION = typeof __TAU_VERSION__ === 'string' ? __TAU_VERSION__ : '0.0.0'
const FRONTEND_BUILD = typeof __TAU_BUILD__ === 'string' ? __TAU_BUILD__ : 'dev'

// Slow on purpose. This never changes without a redeploy, so polling it hard would be pure waste;
// one check a minute is enough to notice a bridge that was updated under a long-lived kiosk tab.
const POLL_MS = 60000

/**
 * This client's version, the bridge's version, and whether they agree.
 *
 * The drift check is the reason this exists rather than a hardcoded string. The frontend is an
 * installable PWA with a service worker (public/sw.js) that caches the bundle, and the kiosk tab
 * may sit open for weeks - so a device can quietly keep serving an old build against a redeployed
 * bridge, with no symptom until something behaves oddly. Comparing the build stamp the bundle was
 * compiled with against the one /api/health reports turns that into something visible.
 *
 * Degrades quietly: if the bridge is unreachable or reports no version, `stale` is false. An
 * unknown version is not evidence of drift, and the footer must never cry wolf at a kiosk that is
 * merely offline for a moment.
 */
export function useVersion() {
  const [bridge, setBridge] = useState(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/health`)
        if (!res.ok) return
        const body = await res.json()
        if (!cancelled) setBridge(body)
      } catch {
        /* best-effort: version reporting must never be load-bearing */
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  // Only claim drift on a positive mismatch of two *known* values - never on absence, and never
  // on a placeholder. Both sides have sentinels for "nobody told me": the bundle reads 'dev' when
  // unstamped, and tau-core reports UNKNOWN_VERSION ('0.0.0+unknown') when it isn't pip-installed,
  // which is exactly how a source checkout runs. Comparing against either would mark every dev
  // machine permanently stale, and a warning that is always on is a warning nobody reads.
  const bridgeBuild = bridge?.build
  const bridgeVersion = bridge?.version
  const buildsComparable =
    Boolean(bridgeBuild) && bridgeBuild !== 'dev' && FRONTEND_BUILD !== 'dev'
  const versionsComparable =
    Boolean(bridgeVersion) && !String(bridgeVersion).endsWith('+unknown')
  const stale =
    (buildsComparable && bridgeBuild !== FRONTEND_BUILD) ||
    (versionsComparable && bridgeVersion !== FRONTEND_VERSION)

  return {
    version: FRONTEND_VERSION,
    build: FRONTEND_BUILD,
    bridgeVersion: bridgeVersion || null,
    bridgeBuild: bridgeBuild || null,
    stale,
    // null until the first successful poll resolves, so the footer never flashes an
    // "unauthenticated" tag before it actually knows (Phase 27.A: /api/health's new field).
    deviceTokenEnforced: bridge ? Boolean(bridge.device_token_enforced) : null,
  }
}
