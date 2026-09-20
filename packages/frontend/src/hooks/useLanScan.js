import { useCallback, useState } from 'react'
import { isTauri } from '../utils/tauri'

/**
 * On-demand (not polled - shelling out to `arp -a` on a timer for a table that barely changes is
 * wasted work) native ARP scan via tau-desktop's scan_lan_arp command. Tauri-only: reading the
 * OS's neighbor table has no web equivalent at all.
 */
export function useLanScan() {
  const [entries, setEntries] = useState([])
  const [error, setError] = useState(null)
  const [scanning, setScanning] = useState(false)

  const scan = useCallback(async () => {
    if (!isTauri()) return
    setScanning(true)
    try {
      const { invoke } = await import('@tauri-apps/api/core')
      setEntries(await invoke('scan_lan_arp'))
      setError(null)
    } catch (e) {
      setError(e?.message || String(e))
    } finally {
      setScanning(false)
    }
  }, [])

  return { entries, error, scanning, scan }
}
