import React, { useEffect, useRef, useState } from 'react'
import { getApiBase } from '../hooks/useMCPResource'
import './BootScreen.css'

const GLYPH_MS = 1800 // tau stroke draw
const TAIL_MS = 900 // "αυ" growing in after it, overlapping the tail end of the stroke slightly
const TAIL_START_MS = GLYPH_MS - 300
const HEALTH_TIMEOUT_MS = 4000 // per attempt - a hung request shouldn't stall the retry loop
const RETRY_INTERVAL_MS = 3000 // pause between attempts once one has failed
const REVEAL_STEP_MS = 140 // per-server reveal in the status line
const HOLD_MS = 500
const FADE_MS = 300

function friendlyServerName(name) {
  return name.replace(/-mcp-server$/, '').replace(/-/g, ' ').toUpperCase()
}

// A double helix built from sampled points, not hand-drawn bezier curves - two sine strands 180°
// out of phase plus evenly-spaced rungs between them. Repeated 3 periods wide so the middle period
// can scroll seamlessly (translate by exactly one period, then snap back) without a visible seam.
function buildHelix({ periods = 10, periodPx = 70, amplitude = 13, centerY = 20, samplesPerPeriod = 24, rungsPerPeriod = 2 }) {
  const y = (x, sign) => centerY + sign * amplitude * Math.sin((x / periodPx) * Math.PI * 2)
  const totalSamples = periods * samplesPerPeriod
  const ptsA = []
  const ptsB = []
  for (let i = 0; i <= totalSamples; i++) {
    const x = (i / samplesPerPeriod) * periodPx
    ptsA.push([x, y(x, 1)])
    ptsB.push([x, y(x, -1)])
  }
  const toPath = (pts) => pts.map(([x, py], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${py.toFixed(1)}`).join(' ')
  const rungCount = periods * rungsPerPeriod
  const rungs = []
  for (let i = 0; i <= rungCount; i++) {
    const x = (i / rungsPerPeriod) * periodPx
    rungs.push({ x, y1: y(x, 1), y2: y(x, -1) })
  }
  return { pathA: toPath(ptsA), pathB: toPath(ptsB), rungs, width: periods * periodPx, periodPx }
}

const HELIX = buildHelix({})

function HelixLayer({ className }) {
  return (
    <svg
      className={className}
      viewBox={`0 0 ${HELIX.width} 40`}
      width={HELIX.width}
      height="40"
      preserveAspectRatio="none"
    >
      <path d={HELIX.pathA} />
      <path d={HELIX.pathB} />
      {HELIX.rungs.map((r, i) => (
        <line key={i} x1={r.x} y1={r.y1} x2={r.x} y2={r.y2} />
      ))}
    </svg>
  )
}

/**
 * Plays once per app mount, ahead of the connection gate / main UI (App.jsx renders it as an
 * early return). The glyph is decorative, but the loading strip beneath it is not: it makes a
 * real GET /api/health call and narrates what actually comes back - which servers are really
 * connected, how many, and honestly says so if tau-core doesn't answer at all - rather than a
 * scripted "ONLINE/ARMED/READY" list that would say the same thing whether or not anything behind
 * it was actually working.
 *
 * An unreachable backend does NOT fall through into a main UI with nothing behind it - the app is
 * useless without tau-core, so this stays on the loading screen and keeps retrying (every
 * RETRY_INTERVAL_MS, each attempt bounded by HEALTH_TIMEOUT_MS) until it actually answers. The one
 * way out short of that is the existing tap-to-skip - kept deliberately, since a first-run Tauri
 * client with no host paired yet would otherwise never reach the "where's tau-core?" connection
 * gate that App.jsx shows after this screen.
 *
 * Progress is a single DNA-helix strip rather than one bar per line: two sine strands scroll
 * continuously (the "still working" cue), and the ink-colored copy is clipped to the real
 * fraction complete - so how far the strand has "resolved" out of the scrolling blur IS the
 * progress bar, not a separate element bolted on next to it.
 */
export default function BootScreen({ onDone }) {
  const [fading, setFading] = useState(false)
  const [progress, setProgress] = useState(4)
  const [statusText, setStatusText] = useState('REACHING TAU CORE…')
  const onDoneRef = useRef(onDone)
  useEffect(() => {
    onDoneRef.current = onDone
  }, [onDone])

  useEffect(() => {
    let cancelled = false
    const timers = []
    const sleep = (ms) => new Promise((r) => timers.push(setTimeout(r, ms)))
    const startedAt = performance.now()
    // The loading area only becomes visible once the glyph finishes drawing (its own CSS timeline,
    // independent of this real data fetch) - without this gate, a fast-answering backend could
    // race straight through the whole reveal sequence and dismiss the boot screen while the tau
    // stroke is still mid-draw, which looks broken rather than fast.
    const glyphDoneAt = GLYPH_MS + TAIL_MS + 200
    const waitForGlyph = () => sleep(Math.max(0, glyphDoneAt - (performance.now() - startedAt)))

    const settle = async (finalText) => {
      if (cancelled) return
      await waitForGlyph()
      if (cancelled) return
      setStatusText(finalText)
      setProgress(100)
      timers.push(setTimeout(() => !cancelled && setFading(true), HOLD_MS))
      timers.push(setTimeout(() => onDoneRef.current?.(), HOLD_MS + FADE_MS))
    }

    const fetchHealth = async () => {
      const controller = new AbortController()
      const abortTimer = setTimeout(() => controller.abort(), HEALTH_TIMEOUT_MS)
      try {
        const res = await fetch(`${getApiBase()}/api/health`, { signal: controller.signal })
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        return await res.json()
      } finally {
        clearTimeout(abortTimer)
      }
    }

    const run = async () => {
      let health = null
      let attempt = 0
      while (!cancelled) {
        attempt += 1
        try {
          health = await fetchHealth()
          break
        } catch {
          if (cancelled) return
          setProgress(6)
          setStatusText(
            attempt === 1
              ? 'TAU CORE UNREACHABLE — RETRYING…'
              : `TAU CORE UNREACHABLE — RETRY ${attempt}…`
          )
          await sleep(RETRY_INTERVAL_MS)
        }
      }
      if (cancelled) return
      await waitForGlyph()
      if (cancelled) return

      const connected = health.connected_servers || []
      const unavailable = health.unavailable_servers || []
      setProgress(30)
      setStatusText(`TAU CORE ONLINE — ${connected.length} DOMAIN SERVER${connected.length === 1 ? '' : 'S'}`)

      for (let i = 0; i < connected.length; i++) {
        if (cancelled) return
        await new Promise((r) => timers.push(setTimeout(r, REVEAL_STEP_MS)))
        if (cancelled) return
        setStatusText(`CONNECTED: ${friendlyServerName(connected[i])}`)
        setProgress(30 + ((i + 1) / connected.length) * 65)
      }

      const summary =
        unavailable.length > 0
          ? `${connected.length} SERVERS READY — ${unavailable.length} UNAVAILABLE`
          : `${connected.length} SERVERS READY`
      settle(summary)
    }
    run()

    return () => {
      cancelled = true
      timers.forEach(clearTimeout)
    }
  }, [])

  const skip = () => {
    setFading(true)
    setTimeout(onDone, FADE_MS)
  }

  return (
    <div
      className={`boot-screen ${fading ? 'fading' : ''}`}
      onClick={skip}
      onKeyDown={skip}
      role="button"
      tabIndex={0}
      aria-label="Skip boot animation"
    >
      <div className="boot-glyph-row">
        <svg className="boot-glyph" viewBox="0 0 100 100" width="120" height="120">
          {/* Lowercase tau: a top crossbar plus a stem that hooks left at the base - not a plain
              "T", which is what a straight stem would read as. */}
          <path d="M18,26 L82,26 M50,26 L50,72 Q50,85 37,84" />
        </svg>
        <span className="boot-tail" style={{ animationDelay: `${TAIL_START_MS}ms` }}>
          αυ
        </span>
      </div>
      <div className="boot-loading" style={{ animationDelay: `${GLYPH_MS + TAIL_MS - 400}ms` }}>
        <div className="boot-helix">
          <div className="boot-helix-track">
            <HelixLayer className="boot-helix-layer boot-helix-base" />
          </div>
          <div
            className="boot-helix-track boot-helix-progress"
            style={{ clipPath: `inset(0 ${100 - progress}% 0 0)` }}
          >
            <HelixLayer className="boot-helix-layer boot-helix-lit" />
          </div>
        </div>
        <div className="boot-status-text">{statusText}</div>
      </div>
    </div>
  )
}
