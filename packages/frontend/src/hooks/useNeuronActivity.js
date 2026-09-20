import { useEffect, useRef, useState } from 'react'
import { getApiBase } from './useMCPResource'

// Human-readable name per server, matching the language useModelActivity uses so the neuron
// labels and the indicator under the atom describe the same work the same way.
export const SERVER_LABEL = {
  'memory-mcp-server': 'MEMORY',
  'vision-mcp-server': 'VISION',
  'home-assistant-mcp-server': 'DEVICES',
  'proxmox-mcp-server': 'INFRA',
  'security-mcp-server': 'SECURITY',
  'fabrication-mcp-server': 'PRINTER',
  'robotics-mcp-server': 'DRONE',
  'voice-mcp-server': 'VOICE',
  'utility-mcp-server': 'UTILITY',
  'phase4-mcp-server': 'REVIEW',
  'ui-bridge-mcp-server': 'UI',
}

const POLL_MS = 900
// How long a neuron stays on the field after its last firing before fading out.
export const DECAY_MS = 9000

/**
 * Pick out the audit events this client hasn't seen before, oldest-first. Mutates `seen`.
 *
 * `seeded=false` means this is the first poll: every event is "new" to us, but none of them are
 * new to Tau, so they're recorded and dropped. Without this, opening the page mid-history would
 * flash a burst of neurons for tool calls that happened minutes ago.
 *
 * Pure (given the set) and exported so this - the part that decides what counts as "happening
 * now" - can be tested without a browser or a bridge.
 */
export function selectFreshEvents(events, seen, seeded) {
  const fresh = []
  // The feed is newest-first; walk it oldest-first so a node ends up recording its newest call.
  for (let i = events.length - 1; i >= 0; i--) {
    const ev = events[i]
    const key = `${ev.timestamp}|${ev.server}|${ev.tool}`
    if (seen.has(key)) continue
    seen.add(key)
    if (seeded) fresh.push(ev)
  }
  return fresh
}

/** Fold fresh events into the node set: one neuron per server, refiring on each new call. */
export function mergeNodes(prev, fresh, now) {
  const byServer = new Map(prev.map((n) => [n.server, n]))
  for (const ev of fresh) {
    byServer.set(ev.server, {
      server: ev.server,
      label: SERVER_LABEL[ev.server] || String(ev.server || '?').toUpperCase(),
      tool: ev.tool,
      outcome: ev.outcome || 'unknown',
      firedAt: now,
    })
  }
  return [...byServer.values()]
}

/** Drop neurons that haven't fired within DECAY_MS. Returns `prev` unchanged if nothing decayed. */
export function decayNodes(prev, now) {
  const cutoff = now - DECAY_MS
  const kept = prev.filter((n) => n.firedAt > cutoff)
  return kept.length === prev.length ? prev : kept
}

/**
 * The live picture behind the neuron field (Phase 6.C second pass): which servers Tau is actually
 * touching this turn, and when each one last fired.
 *
 * Reads the same `/api/activity` audit feed ActivityPanel and useModelActivity read - no new
 * backend plumbing, and it inherits the audit trail's honesty: a neuron only fires because a tool
 * call really happened and was really logged, including calls the CDG denied or queued.
 *
 * Two deliberate details:
 * - **Event-change driven, not timestamp-parsed** (same discipline as useModelActivity): freshness
 *   comes from an event being newly *seen* by this client, so it never depends on the audit
 *   clock's timezone agreeing with the browser's.
 * - **The first poll seeds without firing.** Otherwise opening the page mid-history would flash a
 *   burst of neurons for tool calls that happened minutes ago.
 */
export function useNeuronActivity(active) {
  const [nodes, setNodes] = useState([])
  const seen = useRef(new Set())
  const seeded = useRef(false)

  useEffect(() => {
    if (!active) return undefined
    let cancelled = false

    const poll = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/activity?limit=12`)
        if (!res.ok) return
        const events = await res.json()
        if (cancelled) return

        const fresh = selectFreshEvents(events, seen.current, seeded.current)
        seeded.current = true
        if (fresh.length === 0) return

        const now = Date.now()
        setNodes((prev) => mergeNodes(prev, fresh, now))
      } catch {
        /* best-effort: the field is a visualization, never load-bearing */
      }
    }

    poll()
    const pollId = setInterval(poll, POLL_MS)
    // Separate, slower sweep: decay is time-based, so it has to run even when no new events
    // arrive, otherwise a finished turn's neurons would hang on screen until the next call.
    const decayId = setInterval(() => {
      setNodes((prev) => decayNodes(prev, Date.now()))
    }, 1000)

    return () => {
      cancelled = true
      clearInterval(pollId)
      clearInterval(decayId)
    }
  }, [active])

  // Clear the field between turns so one turn's work never reads as the next turn's.
  useEffect(() => {
    if (!active) setNodes([])
  }, [active])

  return nodes
}
