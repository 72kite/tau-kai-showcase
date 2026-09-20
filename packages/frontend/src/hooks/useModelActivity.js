import { useEffect, useRef, useState } from 'react'
import { getApiBase } from './useMCPResource'

// Map a server name to what the model is plausibly *doing* when it calls that server, so the
// indicator reads in human terms ("searching memory") instead of "memory-mcp-server.match_face".
const SERVER_PHRASE = {
  'memory-mcp-server': 'searching memory',
  'vision-mcp-server': 'looking through cameras',
  'home-assistant-mcp-server': 'checking devices',
  'proxmox-mcp-server': 'checking infrastructure',
  'security-mcp-server': 'checking security',
  'fabrication-mcp-server': 'checking the printer',
  'robotics-mcp-server': 'checking the drone',
  'voice-mcp-server': 'processing speech',
  'utility-mcp-server': 'checking the time',
  'phase4-mcp-server': 'reviewing changes',
}

const POLL_MS = 1200
// How long a just-seen tool call keeps showing its phrase before falling back to the turn phase.
const SHOW_MS = 3000

/**
 * Derives a short "what is the model doing right now" phrase (Phase 6.C model-activity indicator)
 * from two real signals: the caller's turn phase (thinking) and the newest audit event from
 * /api/activity. No new backend plumbing - it reads the same audit feed ActivityPanel does. It's
 * driven by event *change* (a newly-seen event flashes its phrase for SHOW_MS) rather than by
 * parsing the event's timestamp, so it doesn't depend on the audit clock's timezone. Returns ''
 * when nothing is happening.
 */
export function useModelActivity(mode) {
  const [toolPhrase, setToolPhrase] = useState('')
  const lastKey = useRef(null)
  const clearTimer = useRef(null)

  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/activity?limit=1`)
        if (!res.ok) return
        const events = await res.json()
        if (cancelled) return
        const ev = events[0]
        if (!ev) return
        const key = `${ev.timestamp}|${ev.server}|${ev.tool}`
        if (key !== lastKey.current) {
          lastKey.current = key
          const phrase = SERVER_PHRASE[ev.server]
          if (phrase) {
            setToolPhrase(phrase)
            clearTimeout(clearTimer.current)
            clearTimer.current = setTimeout(() => setToolPhrase(''), SHOW_MS)
          }
        }
      } catch {
        /* best-effort: the indicator is a nicety, never load-bearing */
      }
    }
    poll()
    const id = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(id)
      clearTimeout(clearTimer.current)
    }
  }, [])

  // A live tool call wins; otherwise fall back to the turn phase; otherwise idle (empty).
  if (toolPhrase) return toolPhrase
  if (mode === 'thinking') return 'thinking'
  return ''
}
