import { isTauri } from './tauri'

/**
 * Device capability heuristics for the older-iPad kiosk deployment target
 * (project-tau-plan.md Phase 3). Used to scale back 3D geometry and frame rate rather than
 * detecting a hard model allowlist, since Safari deliberately doesn't expose exact hardware.
 */

export function isLikelyOlderTablet() {
  const ua = navigator.userAgent || ''
  const isIPad =
    /iPad/.test(ua) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)
  const cores = navigator.hardwareConcurrency || 4
  // Older iPads (Air 2, Mini 4, 5th/6th gen) report 2 logical cores; recent ones report 6+.
  return isIPad && cores <= 2
}

export function supportsWebGL2() {
  try {
    const canvas = document.createElement('canvas')
    return !!canvas.getContext('webgl2')
  } catch {
    return false
  }
}

// --- Device identity (Phase 6.D) ------------------------------------------------------------
// Each client owns a stable, opaque id in localStorage. It's sent as X-Tau-Device-Id on every
// bridge request so the server can scope this device's transcript to itself - the fix for the
// live-bring-up privacy leak where every tablet saw everyone's conversation. The id grants
// nothing on its own; what a device may DO is still the CDG/access-tier system's job.

const DEVICE_ID_KEY = 'tau-device-id'
const DEVICE_NAME_KEY = 'tau-device-name'
// Phase 27.A: the bearer token an admin minted for THIS device via the dashboard's APPROVE
// control, then a human pasted in here. Never fetched automatically - see server.py's
// approve_device docstring on why an auto-claim path was rejected.
const DEVICE_TOKEN_KEY = 'tau-device-token'

function randomId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  // Fallback for the rare non-secure-context/old-browser case.
  return 'dev-' + Math.random().toString(36).slice(2) + Date.now().toString(36)
}

export function getDeviceId() {
  let id = localStorage.getItem(DEVICE_ID_KEY)
  if (!id) {
    id = randomId()
    localStorage.setItem(DEVICE_ID_KEY, id)
  }
  return id
}

export function getDeviceName() {
  return localStorage.getItem(DEVICE_NAME_KEY) || ''
}

export function setDeviceName(name) {
  localStorage.setItem(DEVICE_NAME_KEY, name || '')
}

// Phase 27.D: in the Tauri desktop shell, the token lives in the OS's real credential store
// (Windows Credential Manager / Linux Secret Service via the Rust `keyring` crate - see
// src-tauri/src/main.rs's store_device_token/get_device_token commands), not localStorage. But
// getDeviceToken()/setDeviceToken() are called SYNCHRONOUSLY from ~6 places (deviceHeaders(),
// useDeviceId.js) and invoke() is async, so this can't just swap the storage call inline - it
// caches the keychain value in memory instead, loaded once at startup by initDeviceToken()
// (called from main.jsx before the app's first render) and updated in place on every
// setDeviceToken() so reads never need to await anything. The kiosk build never calls
// initDeviceToken() (isTauri() is false there), so this cache stays untouched and every call
// falls through to the original localStorage behavior, unchanged.
let tauriTokenCache = ''

export async function initDeviceToken() {
  const { invoke } = await import('@tauri-apps/api/core')
  tauriTokenCache = (await invoke('get_device_token')) || ''
}

export function getDeviceToken() {
  if (isTauri()) return tauriTokenCache
  return localStorage.getItem(DEVICE_TOKEN_KEY) || ''
}

export function setDeviceToken(token) {
  const trimmed = token || ''
  if (isTauri()) {
    tauriTokenCache = trimmed
    import('@tauri-apps/api/core').then(({ invoke }) => invoke('store_device_token', { token: trimmed }))
    return
  }
  localStorage.setItem(DEVICE_TOKEN_KEY, trimmed)
}

// Header bag to spread into every fetch to the bridge. Harmless on endpoints that ignore it, and
// on a deployment with TAU_REQUIRE_DEVICE_TOKEN explicitly turned off (no longer the default as
// of Phase 48), which never looks at X-Tau-Device-Token at all.
export function deviceHeaders() {
  const headers = { 'X-Tau-Device-Id': getDeviceId() }
  const token = getDeviceToken()
  if (token) headers['X-Tau-Device-Token'] = token
  return headers
}
