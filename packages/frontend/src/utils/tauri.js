/**
 * True when running inside the Tauri desktop shell (Phase 27.D, packages/tau-desktop), never in
 * the browser kiosk build. `__TAURI_INTERNALS__` is the object Tauri v2's webview always injects
 * into every page it loads, regardless of the `app.withGlobalTauri` config flag - unlike
 * `__TAURI__`, which only appears when that flag is on. Checking for it needs no Tauri APIs
 * enabled at all, so the kiosk build (which never runs inside Tauri) is unaffected either way.
 */
export function isTauri() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}
