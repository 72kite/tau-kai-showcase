import React, { useEffect } from 'react'
import { useLanScan } from '../hooks/useLanScan'
import './Panel.css'

/**
 * Desktop-shell-only. Reads the OS's own ARP (neighbor) table - a cache of devices this machine
 * has recently exchanged traffic with, not an active network scan. See scan_lan_arp's own comment
 * in src-tauri/src/system.rs for why a cache read rather than a ping sweep.
 */
export default function LanScanPanel() {
  const { entries, error, scanning, scan } = useLanScan()

  useEffect(() => {
    scan()
  }, [scan])

  return (
    <div className="panel lan-scan-panel">
      <div className="panel-title">LAN NEIGHBORS</div>
      <div className="panel-content">
        <div className="panel-note">
          From this machine's own ARP cache - recently-seen devices only, not a live scan.
        </div>
        {error && <div className="panel-note">Unavailable: {error}</div>}
        <button type="button" className="lan-scan-button" onClick={scan} disabled={scanning}>
          {scanning ? 'SCANNING...' : 'RESCAN'}
        </button>
        {entries.map((e) => (
          <div key={e.ip} className="stat-line">
            <span className="stat-label">{e.ip}</span>
            <span className="stat-value">{e.mac}</span>
          </div>
        ))}
        {!scanning && entries.length === 0 && !error && (
          <div className="panel-note">No entries in the ARP cache yet.</div>
        )}
      </div>
    </div>
  )
}
