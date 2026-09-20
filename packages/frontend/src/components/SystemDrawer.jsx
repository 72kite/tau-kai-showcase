import React, { useRef } from 'react'
import { useFocusTrap } from '../hooks/useFocusTrap'
import DevicePanel from './DevicePanel'
import DeviceGlobe from './DeviceGlobe'
import SecurityPanel from './SecurityPanel'
import VisionPanel from './VisionPanel'
import MemoryPanel from './MemoryPanel'
import ActivityPanel from './ActivityPanel'
import PeoplePanel from './PeoplePanel'
import ConnectionSettings from './ConnectionSettings'
import SystemStatsPanel from './SystemStatsPanel'
import LanScanPanel from './LanScanPanel'
import DrawIn from './DrawIn'
import { isTauri } from '../utils/tauri'
import './SystemDrawer.css'

// Each panel draws in a beat after the one before it, so the drawer reads as a hand working down
// the page rather than six boxes landing at once.
const STAGGER_MS = 70

/**
 * Collapsible drawer for devices/security/vision/people detail. Opened from the top toolbar;
 * slides up from the bottom and overlays nothing on the stage, so it never covers or resizes the
 * atom or the design panel - those stay exactly as they are whether this drawer is open or closed.
 */
export default function SystemDrawer({ open, onClose, uiState, people, peopleError }) {
  // Phase 50: keyboard-only navigation had no story here at all - no focus trap, no initial
  // focus, no way to close it but a mouse click. Called unconditionally (before the early return
  // below, since hooks can't follow one) - useFocusTrap itself no-ops while `open` is false.
  const drawerRef = useRef(null)
  useFocusTrap(drawerRef, { active: open, onEscape: onClose })

  if (!open) return null

  const panels = [
    <DevicePanel key="devices" state={uiState.devices} />,
    <DeviceGlobe key="device-globe" />,
    <SecurityPanel key="security" state={uiState.security} />,
    <VisionPanel key="vision" state={uiState.vision} />,
    <PeoplePanel key="people" people={people} error={peopleError} />,
    <MemoryPanel key="memory" />,
    <ActivityPanel key="activity" />,
    // Desktop-shell-only (Phase 27.D): the kiosk build is always same-origin/build-time
    // configured and has nothing for this panel to change.
    ...(isTauri() ? [<ConnectionSettings key="connection" />] : []),
    // Native-exclusive (src-tauri/src/system.rs) - no web API surfaces host hardware stats or the
    // OS ARP table, so both are Tauri-only, same gating as ConnectionSettings above.
    ...(isTauri() ? [<SystemStatsPanel key="system-stats" />, <LanScanPanel key="lan-scan" />] : []),
  ]

  return (
    <div className="system-drawer-overlay" onClick={onClose}>
      <div
        className="system-drawer"
        ref={drawerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="system-drawer-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="system-drawer-header">
          <span id="system-drawer-title">SYSTEM STATUS</span>
          <button type="button" className="system-drawer-close" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="system-drawer-content">
          {panels.map((panel, i) => (
            <DrawIn key={panel.key} delay={i * STAGGER_MS}>
              {panel}
            </DrawIn>
          ))}
        </div>
      </div>
    </div>
  )
}
