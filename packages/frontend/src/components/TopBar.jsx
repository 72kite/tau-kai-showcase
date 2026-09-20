import React, { useEffect, useState } from 'react'
import { useTheme } from '../hooks/useTheme'
import DrawIn from './DrawIn'
import './TopBar.css'

/**
 * The single top toolbar (Phase 6.F). Everything that used to be split across a thin top strip
 * (clock + theme toggle, 6.C) and a bottom status bar (lockdown/devices/vision/approvals/people/
 * attach/mic/admin) lives here as one bounded unit, so nothing competes with the atom for the
 * center of the screen.
 *
 * The clock ticks from the browser's own wall time (the tablet's real local time) rather than
 * polling the bridge - a per-second network poll for the time would be wasteful, and
 * utility-mcp-server's get_time (6.B) exists for the *model* to know the time, not for the UI to
 * render it.
 *
 * The mic entry is icon-only and hue-tinted rather than a "MIC: LISTENING" label, per 6.C's
 * "status by light, not words" - the text state survives only in its tooltip, for the one case
 * light can't express (voice unsupported on this browser).
 */
export default function TopBar({
  uiState,
  approvalCount = 0,
  peopleCount = 0,
  micSupported = true,
  micStatus = '',
  micLive = false,
  voiceMuted = false,
  onToggleVoiceMute,
  bargeInEnabled = true,
  onToggleBargeIn,
  onOpenDrawer,
  onAttach,
}) {
  const { theme, toggle } = useTheme()
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  // No seconds: a per-second reflow on a wall clock is visual noise and widens the bar.
  const time = now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
  const date = now.toLocaleDateString([], { month: 'short', day: 'numeric' })

  const security = uiState?.security || {}
  const devices = uiState?.devices || {}
  const vision = uiState?.vision || {}

  return (
    // Draws itself across on load (6.C), so the first thing the kiosk does on waking is sketch
    // its own toolbar rather than blink it into existence.
    <DrawIn className="top-bar">
      <div className="top-bar-status">
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">LOCKDOWN</span>
          <span className={`bar-value ${security.lockdown_active ? 'alert' : 'quiet'}`}>
            {security.lockdown_active ? 'ACTIVE' : 'CLEAR'}
          </span>
        </button>
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">DEVICES</span>
          <span className="bar-value">
            {devices.online_count ?? 0}/{devices.device_count ?? 0}
          </span>
        </button>
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">VISION</span>
          <span className={`bar-value ${vision.camera_active ? '' : 'quiet'}`}>
            {vision.camera_active ? 'LIVE' : 'OFF'}
          </span>
        </button>
        <button
          type="button"
          className={`bar-item ${approvalCount > 0 ? 'approval-notice' : ''}`}
          onClick={onOpenDrawer}
        >
          <span className="bar-label">APPROVALS</span>
          <span className="bar-value">{approvalCount}</span>
        </button>
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">PEOPLE</span>
          <span className="bar-value">{peopleCount}</span>
        </button>
      </div>

      <div className="top-bar-tools">
        <button
          type="button"
          className="bar-icon"
          onClick={onAttach}
          title="Attach a file for Tau to read"
          aria-label="Attach a file"
        >
          ⎘
        </button>
        <span
          className={`bar-icon mic-icon ${micLive ? 'live' : ''} ${micSupported ? '' : 'unsupported'}`}
          title={micSupported ? `Microphone: ${micStatus || 'idle'}` : 'Voice input unsupported on this browser'}
          aria-label={micSupported ? `Microphone ${micStatus || 'idle'}` : 'Voice input unsupported'}
        >
          ⏺
        </span>
        {/* Voice-out mute (Phase 13). Monochrome glyph to match the e-ink toolbar - the muted
            state is shown by a strike-through class, not a colored emoji. Reads Tau's short reply
            aloud after a voice-initiated turn; this silences it per device. */}
        <button
          type="button"
          className={`bar-icon voice-out-icon ${voiceMuted ? 'muted' : ''}`}
          onClick={onToggleVoiceMute}
          title={voiceMuted ? 'Spoken replies muted - tap to unmute' : 'Tau speaks replies aloud - tap to mute'}
          aria-label={voiceMuted ? 'Unmute spoken replies' : 'Mute spoken replies'}
          aria-pressed={voiceMuted}
        >
          ♪
        </button>
        {/* Barge-in (Phase 19): the raised-hand glyph reads normally when you can interrupt Tau's
            spoken reply with the wake phrase; struck through when barge-in is off (you must wait
            for the reply to finish). Same "status by light, not words" convention as the mic/mute
            icons. */}
        <button
          type="button"
          className={`bar-icon barge-in-icon ${bargeInEnabled ? '' : 'off'}`}
          onClick={onToggleBargeIn}
          title={
            bargeInEnabled
              ? 'Barge-in on - say the wake phrase to interrupt a spoken reply'
              : 'Barge-in off - the wake phrase waits until Tau finishes speaking'
          }
          aria-label={bargeInEnabled ? 'Turn off barge-in' : 'Turn on barge-in'}
          aria-pressed={bargeInEnabled}
        >
          ✋
        </button>
        <button
          type="button"
          className="bar-icon"
          onClick={toggle}
          title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
          aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}
        >
          {theme === 'dark' ? '☾' : '☀'}
        </button>
        <div className="top-clock" aria-label="current time">
          <span className="top-clock-time">{time}</span>
          <span className="top-clock-date">{date}</span>
        </div>
      </div>
    </DrawIn>
  )
}
