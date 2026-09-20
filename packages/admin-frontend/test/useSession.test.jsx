import { renderHook, act, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useSession } from '../src/hooks/useSession'

describe('useSession', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('starts with no token and not checking when sessionStorage is empty', () => {
    const { result } = renderHook(() => useSession())
    expect(result.current.token).toBeNull()
    expect(result.current.checking).toBe(false)
  })

  it('login stores the token and username on success', async () => {
    fetch.mockResolvedValueOnce({
      ok: true,
      text: async () => JSON.stringify({ token: 'abc123', username: 'zion' }),
    })
    const { result } = renderHook(() => useSession())

    let ok
    await act(async () => {
      ok = await result.current.login('zion', 'correct horse battery staple')
    })

    expect(ok).toBe(true)
    expect(result.current.token).toBe('abc123')
    expect(result.current.username).toBe('zion')
    expect(sessionStorage.getItem('tau-admin-session-token')).toBe('abc123')
  })

  it('login surfaces the server error message on failure', async () => {
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: async () => JSON.stringify({ detail: 'Invalid username or password' }),
    })
    const { result } = renderHook(() => useSession())

    let ok
    await act(async () => {
      ok = await result.current.login('zion', 'wrong')
    })

    expect(ok).toBe(false)
    expect(result.current.token).toBeNull()
    expect(result.current.error).toBe('Invalid username or password')
  })

  it('a token already in sessionStorage is re-verified via /me on mount', async () => {
    sessionStorage.setItem('tau-admin-session-token', 'existing-token')
    fetch.mockResolvedValueOnce({
      ok: true,
      text: async () => JSON.stringify({ username: 'zion' }),
    })

    const { result } = renderHook(() => useSession())
    expect(result.current.checking).toBe(true)

    await waitFor(() => expect(result.current.checking).toBe(false))
    expect(result.current.username).toBe('zion')
    expect(fetch).toHaveBeenCalledWith(
      expect.stringContaining('/me'),
      expect.objectContaining({ headers: { Authorization: 'Bearer existing-token' } })
    )
  })

  it('an expired/invalid stored token is cleared, not trusted', async () => {
    sessionStorage.setItem('tau-admin-session-token', 'stale-token')
    fetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      text: async () => JSON.stringify({ detail: 'Session invalid or expired' }),
    })

    const { result } = renderHook(() => useSession())
    await waitFor(() => expect(result.current.checking).toBe(false))

    expect(result.current.token).toBeNull()
    expect(sessionStorage.getItem('tau-admin-session-token')).toBeNull()
  })

  it('logout clears the token even if the server call fails', async () => {
    fetch
      .mockResolvedValueOnce({ ok: true, text: async () => JSON.stringify({ token: 't', username: 'zion' }) })
      .mockRejectedValueOnce(new Error('network blip'))
    const { result } = renderHook(() => useSession())
    await act(async () => {
      await result.current.login('zion', 'pw')
    })

    await act(async () => {
      await result.current.logout()
    })

    expect(result.current.token).toBeNull()
    expect(sessionStorage.getItem('tau-admin-session-token')).toBeNull()
  })
})
