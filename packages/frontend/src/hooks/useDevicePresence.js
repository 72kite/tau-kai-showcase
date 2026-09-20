import { useCallback, useEffect, useState } from 'react'
import { getApiBase } from './useMCPResource'

// Presence-relevant states from home-assistant-mcp-server.list_devices - anything else (a light,
// a sensor reading) isn't a location signal and is filtered out client-side, since list_devices
// itself returns every entity Home Assistant knows about, not just trackers.
const HOME_STATES = new Set(['home'])
const AWAY_STATES = new Set(['not_home'])

/**
 * Polls home-assistant-mcp-server's list_devices tool (same generic /api/tools passthrough as
 * usePeople.js) and filters to person.* entities for the Device Globe panel. There is no
 * dedicated presence tool server-side - list_devices returns every HA entity, so the filtering
 * happens here rather than adding a new MCP tool for what's a client-side filter.
 *
 * Deliberately person.* only, not device_tracker.* too (an earlier version showed both): a home
 * network's device_tracker list is mostly infrastructure noise - routers, switches, APs, a
 * Minecraft server, every UniFi client the router has ever seen, one row per MAC address, most
 * with no friendly_name at all. person.* is the domain Home Assistant itself built for "is this
 * PERSON home" - it already aggregates someone's real trackers (see the presence fix in
 * project-tau-plan.md's Phase 33 entry), so it's both the more correct signal and the one that
 * doesn't need a brittle name-pattern filter to stay readable.
 */
export function useDevicePresence(pollIntervalMs = 15000) {
  const [home, setHome] = useState([])
  const [away, setAway] = useState([])
  const [unknown, setUnknown] = useState([])
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const res = await fetch(`${getApiBase()}/api/tools/home-assistant-mcp-server/list_devices`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ arguments: {} }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const outcome = await res.json()
      if (outcome.status !== 'executed') throw new Error(outcome.reason || outcome.status)
      // list_devices returns a list, so tau-core's passthrough splits it into one content block
      // PER ITEM (see tau_core/web/server.py's call_tool: "results carries every content block -
      // FastMCP tools returning a list produce one block per item") - `outcome.result` is only
      // the first entity. usePeople.js's `JSON.parse(outcome.result)` pattern only works for tools
      // that return a single JSON object (list_people's `{people: [...]}`), not this one.
      const entities = outcome.results.map((text) => JSON.parse(text))
      const trackers = entities.filter((e) => e.entity_id.startsWith('person.'))
      setHome(trackers.filter((e) => HOME_STATES.has(e.state)))
      setAway(trackers.filter((e) => AWAY_STATES.has(e.state)))
      setUnknown(trackers.filter((e) => !HOME_STATES.has(e.state) && !AWAY_STATES.has(e.state)))
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

  return { home, away, unknown, error, refresh }
}
