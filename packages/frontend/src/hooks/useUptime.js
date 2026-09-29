import { useEffect, useRef, useState } from 'react'
import { getApiBase } from './useMCPResource'

// Slow, for the same reason useVersion's poll is slow: uptime is a number that advances at a
// known rate, so asking the bridge for it every second would be a network round-trip to learn
// something arithmetic already knows. One poll a minute anchors it; the ticker below fills in.
const POLL_MS = 60000

/**
 * tau-core's process uptime, ticking once a second.
 *
 * Anchored, not polled: /api/health's `uptime_seconds` is read once a minute and the value is
 * advanced locally in between - the same division of labour TopBar already uses for the wall
 * clock (browser time for the display, the bridge only for what the browser can't know). A
 * restart therefore shows up within a minute rather than instantly, which is the right trade for
 * a readout whose whole job is "has this been up a while or not".
 *
 * Returns null until the first successful poll, and *keeps the last known value* if a later poll
 * fails - the kiosk goes offline for a moment fairly often (wifi, a bridge redeploy), and
 * blanking a readout that was correct a second ago says "unknown" when the truth is "still
 * counting". A genuinely dead bridge shows up as a uptime that resets, which is the real signal.
 */
export function useUptime() {
  // What the bridge last told us, and the local clock reading when it did. Both in one state
  // object so a render can never see a fresh anchor paired with a stale timestamp.
  const [anchor, setAnchor] = useState(null)
  const [seconds, setSeconds] = useState(null)
  const anchorRef = useRef(null)

  useEffect(() => {
    anchorRef.current = anchor
  }, [anchor])

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/health`)
        if (!res.ok) return
        const body = await res.json()
        const value = Number(body?.uptime_seconds)
        if (!cancelled && Number.isFinite(value)) {
          setAnchor({ seconds: value, at: Date.now() })
        }
      } catch {
        /* best-effort: an uptime readout must never be load-bearing (see useVersion) */
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [])

  useEffect(() => {
    const tick = () => {
      const a = anchorRef.current
      if (!a) return
      setSeconds(a.seconds + (Date.now() - a.at) / 1000)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [])

  return seconds
}

/**
 * "3d 12h", "12h 04m", "04m". Coarsest two units only - a wall-mounted readout is glanced at, not
 * read, and "3d 12h 47m 12s" is four numbers to parse for one fact. Seconds appear only under a
 * minute, so a just-restarted bridge still visibly counts rather than sitting on "0m".
 */
export function formatUptime(totalSeconds) {
  if (totalSeconds == null || !Number.isFinite(totalSeconds)) return null
  const s = Math.max(0, Math.floor(totalSeconds))
  const days = Math.floor(s / 86400)
  const hours = Math.floor((s % 86400) / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  if (days > 0) return `${days}d ${String(hours).padStart(2, '0')}h`
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  if (minutes > 0) return `${minutes}m`
  return `${s}s`
}
