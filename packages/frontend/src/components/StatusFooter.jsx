import React from 'react'
import { useVersion } from '../hooks/useVersion'
import './StatusFooter.css'

/**
 * The far-bottom identity strip: what build this is, and which device you are standing in front of.
 *
 * Why it exists: with several kiosks on a wall, "which one is this and what is it running" had no
 * answer from the screen - the device id was visible only inside the admin dashboard, and nothing
 * in the repo reported a version at all. Both are diagnostics rather than secrets, so they are
 * shown to everyone; ADMIN stays in the top toolbar where 6.F put it.
 *
 * It is deliberately the quietest thing on the page - muted, tiny, no border. 6.F's whole point is
 * that the atom owns the stage, and a footer that competes with it would undo that.
 */
export default function StatusFooter({ deviceId, deviceName }) {
  const { version, build, bridgeVersion, bridgeBuild, stale, deviceTokenEnforced } = useVersion()

  // Mirror DeviceRegistry's own fallback (devices.py: `device-{first8}`), so the name on the glass
  // matches the name in the admin device list rather than inventing a second convention.
  const shortId = (deviceId || '').slice(0, 8)
  const label = deviceName || (shortId ? `device-${shortId}` : 'unregistered')

  return (
    <div className="status-footer">
      {/* Version and its staleness flag stay together - the flag is a statement about the build
          next to it, not a free-floating alarm. */}
      <span className="footer-left">
        <span className="footer-version" title={`bridge: ${bridgeVersion || '—'} (${bridgeBuild || '—'})`}>
          TAU v{version}
          <span className="footer-build">+{build}</span>
        </span>
        {stale && (
          <span
            className="footer-stale"
            title={`This screen is running ${version}+${build} but the bridge is ${bridgeVersion}+${bridgeBuild}. Reload to pick up the current build.`}
          >
            STALE
          </span>
        )}
        {/* Phase 27.A: the open-by-default device-approval state used to be silent. This mirrors
            STALE above rather than a bigger banner - loud enough to notice, quiet enough not to
            fight the atom for the stage (see this file's own docstring). */}
        {deviceTokenEnforced === false && (
          <span
            className="footer-unauthenticated"
            title="TAU_REQUIRE_DEVICE_TOKEN is off: any device can use this bridge with no admin approval."
          >
            UNAUTHENTICATED
          </span>
        )}
      </span>

      <span className="footer-device" title={deviceId || ''}>
        {label}
      </span>
    </div>
  )
}
