import React, { useEffect, useState } from 'react'
import { useTheme } from '../hooks/useTheme'
import { useUptime, formatUptime } from '../hooks/useUptime'
import DrawIn from './DrawIn'
import './TopBar.css'

/**
 * A decorative rule between data clusters.
 *
 * A real element with aria-hidden rather than a CSS ::after on .bar-item: generated content inside
 * a <button> is folded into that button's accessible name by Chrome, so a pipe drawn that way
 * turns "LOCKDOWN CLEAR" into "LOCKDOWN CLEAR |" for a screen reader. Outside the buttons it is
 * invisible to AT and identical on screen.
 *
 * Declared at module scope, NOT inside TopBar. A component defined in a render body is a new
 * component *type* on every render, so React unmounts and remounts its whole subtree each time -
 * and this toolbar re-renders once a second for the clock, which would have churned these five
 * spans 86,400 times a day on a kiosk that never navigates away.
 */
const Pipe = () => (
  <span className="bar-pipe" aria-hidden="true">
    |
  </span>
)

/**
 * The single top toolbar (Phase 6.F). Everything that used to be split across a thin top strip
 * (clock + theme toggle, 6.C) and a bottom status bar (lockdown/devices/vision/approvals/people/
 * attach/mic/admin) lives here as one bounded unit, so nothing competes with the atom for the
 * center of the screen.
 *
 * Three zones, laid out on a grid rather than space-between (Phase 54): system status left,
 * hardware I/O + clock right, and a deliberately empty center column. The grid is what makes the
 * clock *strictly* right-aligned - with space-between it drifted left whenever the status cluster
 * grew or shrank (a device coming online changes "11/12" to "12/12" and the whole right edge
 * moved), which is exactly the wobble a fixed instrument readout must not have.
 *
 * The clock ticks from the browser's own wall time (the tablet's real local time) rather than
 * polling the bridge - a per-second network poll for the time would be wasteful, and
 * utility-mcp-server's get_time (6.B) exists for the *model* to know the time, not for the UI to
 * render it. Uptime is the one part the browser genuinely cannot know, so it comes from
 * /api/health on a slow poll and is ticked locally in between (useUptime).
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
  speaking = false,
  onOpenDrawer,
  onAttach,
}) {
  const { theme, toggle } = useTheme()
  const [now, setNow] = useState(() => new Date())
  const uptimeSeconds = useUptime()

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  // Seconds are shown (Phase 54), reversing 6.F's "no seconds" decision. That decision's stated
  // reason was "a per-second reflow on a wall clock is visual noise and widens the bar" - and it
  // was right about the cause and wrong about the fix. The reflow came from proportional digits
  // changing width, not from the seconds existing: .top-clock-time already sets tabular-nums, so
  // every digit occupies the same box and the field's width is now constant by construction. The
  // readout reads as an instrument rather than a wall clock, which is the point of the zone.
  const time = now.toLocaleTimeString([], {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
  })
  // "TUE 22 SEP" - weekday, then day, then month, the way a technical log stamps a date. Built
  // from individual parts rather than one toLocaleDateString call because the combined call
  // returns the *locale's* field order (en-US gives "Sep 22"), and the point of this readout is a
  // fixed instrument format that reads the same on every device the kiosk ships to.
  const weekday = now.toLocaleDateString([], { weekday: 'short' }).toUpperCase().replace(/\./g, '')
  const day = now.toLocaleDateString([], { day: '2-digit' })
  const month = now.toLocaleDateString([], { month: 'short' }).toUpperCase().replace(/\./g, '')
  const uptime = formatUptime(uptimeSeconds)

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
        <Pipe />
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">DEVICES</span>
          <span className="bar-value">
            {devices.online_count ?? 0}/{devices.device_count ?? 0}
          </span>
        </button>
        <Pipe />
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">VISION</span>
          <span className={`bar-value ${vision.camera_active ? '' : 'quiet'}`}>
            {vision.camera_active ? 'LIVE' : 'OFF'}
          </span>
        </button>
        <Pipe />
        <button
          type="button"
          className={`bar-item ${approvalCount > 0 ? 'approval-notice' : ''}`}
          onClick={onOpenDrawer}
        >
          <span className="bar-label">APPROVALS</span>
          <span className="bar-value">{approvalCount}</span>
        </button>
        <Pipe />
        <button type="button" className="bar-item" onClick={onOpenDrawer}>
          <span className="bar-label">PEOPLE</span>
          <span className="bar-value">{peopleCount}</span>
        </button>
      </div>

      {/* Center column: intentionally empty. It exists so the clock is pinned to the right edge
          regardless of how wide the status cluster gets, and it is where the ambient live widget
          (recognition / now-playing / weather) lands when that slice is built. Nothing is
          rendered here until there is real state to render - a kiosk must never show a
          placeholder someone could mistake for a reading. */}
      <div className="top-bar-center" />

      <div className="top-bar-tools">
        {/* Hardware I/O, as one outlined group. These are the three inputs/outputs a person can
            actually govern from this screen; each shows its own state rather than a label, per
            6.C's "status by light, not words".

            Vision is NOT in this group - it is a readout on the left, because there is no camera
            on/off control on this device to bind a toggle to. Neither is a gesture input, which
            does not exist yet. An outlined button that looks live and controls nothing is worse
            than an absent one. */}
        <div className="io-group" role="group" aria-label="Hardware inputs and outputs">
          <span
            className={`io-toggle io-status ${micLive ? 'processing' : ''} ${
              micSupported ? '' : 'disabled'
            }`}
            role="status"
            title={
              micSupported
                ? `Microphone: ${micStatus || 'idle'}`
                : 'Voice input unsupported on this browser'
            }
            aria-label={
              micSupported ? `Microphone ${micStatus || 'idle'}` : 'Voice input unsupported'
            }
          >
            <span className="io-glyph" aria-hidden="true">
              ⏺
            </span>
            <span className="io-name" aria-hidden="true">
              MIC
            </span>
          </span>

          {/* Voice-out mute (Phase 13). Reads Tau's short reply aloud after a voice-initiated
              turn; this silences it per device. Blinks while playback is actually running, so
              the bar shows the difference between "will speak" and "is speaking". */}
          <button
            type="button"
            className={`io-toggle ${voiceMuted ? 'disabled' : ''} ${
              speaking && !voiceMuted ? 'processing' : ''
            }`}
            onClick={onToggleVoiceMute}
            title={
              voiceMuted
                ? 'Spoken replies muted - tap to unmute'
                : 'Tau speaks replies aloud - tap to mute'
            }
            aria-label={voiceMuted ? 'Unmute spoken replies' : 'Mute spoken replies'}
            aria-pressed={voiceMuted}
          >
            <span className="io-glyph" aria-hidden="true">
              ♪
            </span>
            <span className="io-name" aria-hidden="true">
              OUT
            </span>
          </button>

          {/* Barge-in (Phase 19): on means the wake phrase can interrupt a spoken reply; off
              means you wait for it to finish. */}
          <button
            type="button"
            className={`io-toggle ${bargeInEnabled ? '' : 'disabled'}`}
            onClick={onToggleBargeIn}
            title={
              bargeInEnabled
                ? 'Barge-in on - say the wake phrase to interrupt a spoken reply'
                : 'Barge-in off - the wake phrase waits until Tau finishes speaking'
            }
            aria-label={bargeInEnabled ? 'Turn off barge-in' : 'Turn on barge-in'}
            aria-pressed={bargeInEnabled}
          >
            <span className="io-glyph" aria-hidden="true">
              ✋
            </span>
            <span className="io-name" aria-hidden="true">
              CUT
            </span>
          </button>
        </div>

        {/* Attach and theme are not hardware I/O, so they stay outside the outlined group as
            plain glyph buttons - grouping them would imply they have an on/off state. */}
        <button
          type="button"
          className="bar-icon"
          onClick={onAttach}
          title="Attach a file for Tau to read"
          aria-label="Attach a file"
        >
          ⎘
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

        <div className="top-clock" aria-label="current time and system uptime">
          <span className="top-clock-time">{time}</span>
          <Pipe />
          <span className="top-clock-date">
            {weekday} {day} {month}
          </span>
          {/* Absent, not zeroed, until /api/health has answered once - see useUptime. */}
          {uptime && (
            <>
              <Pipe />
              <span className="top-clock-uptime">UPTIME: {uptime}</span>
            </>
          )}
        </div>
      </div>
    </DrawIn>
  )
}
