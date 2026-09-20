import { useCallback, useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'

/**
 * Polls memory-mcp-server's list_people tool through tau-core's generic tool-call passthrough
 * (POST /api/tools/{server}/{tool}, see tau_core.web.server) to show who Tau currently
 * recognizes. This goes through /api/tools rather than /api/resources like useMCPResource:
 * list_people is an on-demand aggregate memory-mcp-server computes on call, not push-updated
 * state a resource subscription tracks.
 */
export function usePeople(pollIntervalMs = 10000) {
  const [people, setPeople] = useState([])
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/tools/memory-mcp-server/list_people`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arguments: {} }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const outcome = await res.json()
      if (outcome.status !== 'executed') throw new Error(outcome.reason || outcome.status)
      const parsed = JSON.parse(outcome.result)
      setPeople(parsed.people || [])
      setError(null)
    } catch (e) {
      setError(e.message)
    }
  }, [])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, pollIntervalMs)
    return () => clearInterval(id)
  }, [refresh, pollIntervalMs])

  return { people, error, refresh }
}
