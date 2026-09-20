/**
 * Phase 27.D Milestone 3: mDNS discovery + TLS-pairing helpers for the Tauri desktop shell. Every
 * function here is Tauri-only (dynamically imports `@tauri-apps/api/core`, same code-splitting
 * reasoning as `device.js`'s token cache) - the kiosk build never calls any of these.
 *
 * The paired host/port is NOT a secret (it's a LAN address), so it lives in plain localStorage,
 * same tier as the API base override. The certificate fingerprint IS the security-relevant value
 * and is pinned via the OS keychain instead - see main.rs's pin_certificate/get_pinned_fingerprint
 * commands, which store it the same way utils/device.js stores the device token.
 */

const PAIRED_HOST_KEY = 'tau-paired-host'

export function getPairedHost() {
  const raw = localStorage.getItem(PAIRED_HOST_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw)
  } catch {
    return null
  }
}

export function setPairedHost(host, port) {
  localStorage.setItem(PAIRED_HOST_KEY, JSON.stringify({ host, port }))
}

export function clearPairedHost() {
  localStorage.removeItem(PAIRED_HOST_KEY)
}

export async function discoverTauCore() {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke('discover_tau_core')
}

export async function fetchPairingFingerprint(host, port) {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke('fetch_pairing_fingerprint', { host, port })
}

export async function pinCertificate(fingerprint) {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke('pin_certificate', { fingerprint })
}

export async function getPinnedFingerprint() {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke('get_pinned_fingerprint')
}

export async function clearPinnedFingerprint() {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke('clear_pinned_fingerprint')
}

/** Returns the local proxy's port (see proxy.rs) - point getApiBase() at
 * `http://127.0.0.1:<port>` once this resolves. */
export async function startPinningProxy(host, port, fingerprint) {
  const { invoke } = await import('@tauri-apps/api/core')
  return invoke('start_pinning_proxy', {
    targetHost: host,
    targetPort: port,
    pinnedFingerprint: fingerprint,
  })
}
