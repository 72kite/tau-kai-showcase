import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

describe('adminToken', () => {
  beforeEach(() => {
    vi.resetModules()
  })
  afterEach(() => {
    vi.doUnmock('../src/utils/tauri')
  })

  it('non-Tauri: reads/writes sessionStorage under the expected key', async () => {
    vi.doMock('../src/utils/tauri', () => ({ isTauri: () => false }))
    const { getAdminToken, setAdminToken } = await import('../src/utils/adminToken')

    expect(getAdminToken()).toBe('')
    setAdminToken('abc123')
    expect(sessionStorage.getItem('tau-admin-session-token')).toBe('abc123')
    expect(getAdminToken()).toBe('abc123')

    setAdminToken('')
    expect(sessionStorage.getItem('tau-admin-session-token')).toBeNull()
  })

  it('Tauri: setAdminToken invokes store_admin_token, getAdminToken reads the in-memory cache', async () => {
    const invoke = vi.fn().mockResolvedValue(undefined)
    vi.doMock('../src/utils/tauri', () => ({ isTauri: () => true }))
    vi.doMock('@tauri-apps/api/core', () => ({ invoke }))
    const { getAdminToken, setAdminToken, initAdminToken } = await import('../src/utils/adminToken')

    setAdminToken('xyz789')
    // getAdminToken reads the synchronous cache immediately - it must not wait on the invoke().
    expect(getAdminToken()).toBe('xyz789')
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith('store_admin_token', { token: 'xyz789' }))

    setAdminToken('')
    await vi.waitFor(() => expect(invoke).toHaveBeenCalledWith('clear_admin_token'))

    invoke.mockResolvedValueOnce('restored-token')
    await initAdminToken()
    expect(getAdminToken()).toBe('restored-token')
  })

  it('non-Tauri: a storage exception (private browsing, etc.) degrades to no token rather than throwing', async () => {
    vi.doMock('../src/utils/tauri', () => ({ isTauri: () => false }))
    const { getAdminToken, setAdminToken } = await import('../src/utils/adminToken')

    const original = window.sessionStorage
    Object.defineProperty(window, 'sessionStorage', {
      configurable: true,
      get() {
        throw new Error('storage blocked')
      },
    })

    expect(() => getAdminToken()).not.toThrow()
    expect(getAdminToken()).toBe('')
    expect(() => setAdminToken('x')).not.toThrow()

    Object.defineProperty(window, 'sessionStorage', { configurable: true, value: original })
  })
})
