import { useCallback, useEffect, useState } from 'react'
import { api, ApiError } from '../api'
import { getAdminToken, setAdminToken } from '../utils/adminToken'

// Storage is genuinely hardware-backed (OS Credential Manager / Secret Service via the Rust
// `keyring` crate) inside the tau-admin-desktop Tauri shell, and sessionStorage otherwise - see
// utils/adminToken.js for the full seam. The plain-browser fallback is a pragmatic lesser
// default, not the "secure sector" storage the kiosk's device token gets - it isn't picked up by
// "restore previous session" flows and doesn't persist past the browser closing, which narrows
// (does not eliminate) the XSS exposure window sessionStorage still carries.
export function useSession() {
  const [token, setTokenState] = useState(() => getAdminToken() || null)
  const [username, setUsername] = useState(null)
  const [checking, setChecking] = useState(Boolean(token))
  const [error, setError] = useState(null)

  const setToken = useCallback((value) => {
    setTokenState(value)
    setAdminToken(value)
  }, [])

  // On load, a token surviving a page refresh (same tab) needs to be re-checked - it may have
  // expired server-side since the last request.
  useEffect(() => {
    if (!token) {
      setChecking(false)
      return
    }
    let cancelled = false
    api
      .me(token)
      .then((body) => {
        if (!cancelled) setUsername(body.username)
      })
      .catch(() => {
        if (!cancelled) setToken(null)
      })
      .finally(() => {
        if (!cancelled) setChecking(false)
      })
    return () => {
      cancelled = true
    }
    // Intentionally only on mount - re-verifying on every token change would re-run this right
    // after login() below already learned the username from the response body.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const login = useCallback(
    async (usernameInput, password) => {
      setError(null)
      try {
        const body = await api.login(usernameInput, password)
        setToken(body.token)
        setUsername(body.username)
        return true
      } catch (e) {
        setError(e instanceof ApiError ? e.message : 'Could not reach the admin server')
        return false
      }
    },
    [setToken]
  )

  const logout = useCallback(async () => {
    if (token) {
      try {
        await api.logout(token)
      } catch {
        // Best-effort - the client-side token clear below is what actually matters locally.
      }
    }
    setToken(null)
    setUsername(null)
  }, [token, setToken])

  return { token, username, checking, error, login, logout }
}
