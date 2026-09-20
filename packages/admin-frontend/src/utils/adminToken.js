import { isTauri } from './tauri'

/**
 * The admin session token's storage: OS keychain (Windows Credential Manager / Linux Secret
 * Service, via tau-admin-desktop's store_admin_token/get_admin_token/clear_admin_token Rust
 * commands) when running inside that Tauri shell, sessionStorage otherwise - the plain-browser
 * fallback useSession.js's own comment already documents as a lesser default. This file is the
 * seam that used to be a TODO ("wrapping this app in its own Tauri shell... is the concrete next
 * step") - now that the shell exists, this is where the seam actually gets used.
 *
 * Same synchronous-cache-loaded-at-startup pattern as packages/frontend/src/utils/device.js's
 * getDeviceToken()/setDeviceToken(): invoke() is async, but useSession's `useState(() =>
 * sessionStorage.getItem(...))` initializer and every other read site need a synchronous answer.
 * initAdminToken() must be awaited before the app's first render (see main.jsx) so the cache is
 * never read before it's loaded.
 */
const SESSION_STORAGE_KEY = 'tau-admin-session-token'

let tauriTokenCache = ''

export async function initAdminToken() {
  const { invoke } = await import('@tauri-apps/api/core')
  tauriTokenCache = (await invoke('get_admin_token')) || ''
}

export function getAdminToken() {
  if (isTauri()) return tauriTokenCache
  try {
    return sessionStorage.getItem(SESSION_STORAGE_KEY) || ''
  } catch {
    return '' // private-browsing/storage-blocked edge case - fail to "no token", not a crash
  }
}

export function setAdminToken(token) {
  const trimmed = token || ''
  if (isTauri()) {
    tauriTokenCache = trimmed
    import('@tauri-apps/api/core').then(({ invoke }) =>
      trimmed ? invoke('store_admin_token', { token: trimmed }) : invoke('clear_admin_token')
    )
    return
  }
  try {
    if (trimmed) sessionStorage.setItem(SESSION_STORAGE_KEY, trimmed)
    else sessionStorage.removeItem(SESSION_STORAGE_KEY)
  } catch {
    // Same as above - storage being unavailable must not crash login/logout.
  }
}
