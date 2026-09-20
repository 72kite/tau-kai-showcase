/**
 * True when running inside the tau-admin-desktop Tauri shell (Phase 40), never in the plain
 * browser build. Identical check to packages/frontend/src/utils/tauri.js - see that file's own
 * comment for why `__TAURI_INTERNALS__` (not `__TAURI__`) is the right thing to test.
 */
export function isTauri() {
  return typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
}
