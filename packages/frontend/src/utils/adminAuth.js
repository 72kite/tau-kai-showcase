import { deviceHeaders } from './device'

/**
 * Phase 16 admin portal hardening: the admin voice token travels as a request header, never in a
 * URL query string or JSON body - both leak into server/proxy logs and browser history in a way
 * a header doesn't. Every admin-gated fetch in the frontend goes through this one helper so the
 * transport can't drift back to a query param in just one hook.
 *
 * Phase 27.A: also spreads this browser's own device headers. Every admin-gated route now also
 * runs `_device_id()` (server.py), so the admin dashboard's own fetches need to carry them too,
 * or a device-token-enforcing install would 403 the dashboard on its own admin session.
 */
export function authHeaders(token) {
  return { ...deviceHeaders(), ...(token ? { 'X-Tau-Voice-Token': token } : {}) }
}
