import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { isTauri } from './utils/tauri'
import { initDeviceToken } from './utils/device'
import { setApiBase } from './hooks/useMCPResource'
import { discoverTauCore, fetchPairingFingerprint, getPairedHost, getPinnedFingerprint, setPairedHost, startPinningProxy } from './utils/pairing'
import './index.css'

function renderApp() {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  )
}

// Verifies host:port is still presenting the pinned fingerprint - fetchPairingFingerprint makes
// an unverified TLS connection purely to capture what certificate is actually there (pairing.rs),
// same as the human-facing pairing flow in ConnectionSettings.jsx, just compared automatically
// here instead of shown for eyeball confirmation. Only on an exact match does it start the local
// pinning proxy and point the app at it - unlike the old unconditional version of this function,
// nothing gets pointed at a host until its identity has actually been checked.
async function pinAndConnect(host, port, fingerprint) {
  try {
    const actual = await fetchPairingFingerprint(host, port)
    if (actual !== fingerprint) return false
    const localPort = await startPinningProxy(host, port, fingerprint)
    setApiBase(`http://127.0.0.1:${localPort}`)
    return true
  } catch {
    return false
  }
}

// Phase 27.D Milestone 3: if a previous session paired with a tau-core host (mDNS discovery +
// TLS fingerprint confirmation - see ConnectionSettings.jsx), restart the pinning proxy against
// it before rendering, so the app boots straight back into that connection instead of showing
// the manual/discovery UI again on every launch.
//
// Follow-up: the paired host's IP can drift between launches (DHCP lease renewal, sleep/wake on a
// different network) - tau_core/discovery.py always advertises under the same fixed instance name
// ("tau-core"), not a host-derived one, so a stale IP is recoverable by re-browsing rather than a
// dead end. If the last-known address no longer answers with the pinned fingerprint, re-discover
// and retry against every service found; the first one whose certificate still matches the pin
// wins and its address is persisted as the new paired host. A changed fingerprint is never
// auto-trusted - that candidate is just skipped, same as an unreachable one. Best-effort
// throughout: if nothing pans out, getApiBase() stays whatever it already was - App.jsx's existing
// first-run gate or the last-used manual override - not a crash.
async function reconnectIfPaired() {
  const paired = getPairedHost()
  if (!paired) return
  const fingerprint = await getPinnedFingerprint()
  if (!fingerprint) return

  if (await pinAndConnect(paired.host, paired.port, fingerprint)) return

  try {
    const services = await discoverTauCore()
    for (const service of services) {
      if (await pinAndConnect(service.host, service.port, fingerprint)) {
        setPairedHost(service.host, service.port)
        return
      }
    }
  } catch {
    // Best-effort - see comment above.
  }
}

// Phase 27.D: the Tauri shell's device-token cache (utils/device.js) must be loaded from the OS
// keychain before the app's first render, so useDeviceId()'s `useState(() => Boolean
// (getDeviceToken()))` never reads a not-yet-loaded cache. The kiosk build never awaits anything
// here - isTauri() is false, so it renders immediately exactly as before.
if (isTauri()) {
  Promise.all([initDeviceToken(), reconnectIfPaired()]).finally(renderApp)
} else {
  renderApp()
}

// Project Archer (mobile companion, project-tau-plan.md Phase 5): registering the service
// worker here makes this same frontend installable on a phone via "Add to Home Screen",
// reaching tau-core's web bridge over Tailscale/WireGuard instead of the home LAN. Non-fatal
// if registration fails - the app works identically without it, just without an instant-open
// offline shell.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
