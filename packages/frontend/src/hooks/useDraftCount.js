import { useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'

/**
 * How many draft memories are awaiting review, for the ADMIN badge (Phase 9).
 *
 * The nudge §8.7 flagged as missing: the review queue existed, but nothing told anyone it had
 * grown, so "batch-reviewed" still depended on somebody deciding to go and look. Tau writes
 * notes about the household; the queue needs to ask for attention, not wait for it.
 *
 * Polled slowly (60s), and that is a deliberate number rather than a default. Every poll is a
 * real `list_drafts` call through host.call_tool, so it lands in the CDG pipeline and the audit
 * trail like any other - at the 2-4s cadence the rest of this UI uses, a handful of idle kiosks
 * would bury the activity feed under thousands of "checked the draft queue" events. Drafts
 * accumulate over hours; a minute of staleness on a badge costs nothing.
 */
export function useDraftCount(pollIntervalMs = 60000) {
  const [count, setCount] = useState(0)

  useEffect(() => {
    let cancelled = false

    const refresh = async () => {
      try {
        const res = await fetch(`${getApiBase()}/api/drafts/count`)
        if (!res.ok) return
        const body = await res.json()
        if (!cancelled) setCount(body.count || 0)
      } catch {
        // A badge must never be the thing that breaks the toolbar; a stale count is fine.
      }
    }

    refresh()
    const id = setInterval(refresh, pollIntervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [pollIntervalMs])

  return count
}
