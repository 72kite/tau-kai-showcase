import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import UserProfilesPanel from '../src/components/UserProfilesPanel'

function jsonResponse(body, ok = true) {
  return { ok, status: ok ? 200 : 400, text: async () => JSON.stringify(body) }
}

describe('UserProfilesPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders the roster from /api/admin/people', async () => {
    fetch.mockResolvedValueOnce(
      jsonResponse({
        people: [{ person_id: 'zion', access_level: 'admin', face_count: 1, voice_count: 1 }],
      })
    )
    render(<UserProfilesPanel token="tok" />)

    expect(await screen.findByText('zion')).toBeInTheDocument()
    expect(screen.getByText('People (1)')).toBeInTheDocument()
  })

  it('submitting an access-level change that comes back pending_approval shows Continue', async () => {
    fetch
      .mockResolvedValueOnce(
        jsonResponse({ people: [{ person_id: 'zion', access_level: 'standard' }] })
      )
      .mockResolvedValueOnce(
        jsonResponse({ status: 'pending_approval', approval_request_id: 'abc' })
      )
    render(<UserProfilesPanel token="tok" />)
    await screen.findByText('zion')

    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))

    expect(await screen.findByRole('button', { name: 'Continue' })).toBeInTheDocument()
  })

  it('clicking Continue re-issues the call with the approval id and refreshes', async () => {
    fetch
      .mockResolvedValueOnce(
        jsonResponse({ people: [{ person_id: 'zion', access_level: 'standard' }] })
      )
      .mockResolvedValueOnce(
        jsonResponse({ status: 'pending_approval', approval_request_id: 'abc' })
      )
      .mockResolvedValueOnce(jsonResponse({ status: 'ok' }))
      .mockResolvedValueOnce(
        jsonResponse({ people: [{ person_id: 'zion', access_level: 'admin' }] })
      )
    render(<UserProfilesPanel token="tok" />)
    await screen.findByText('zion')
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'admin' } })
    fireEvent.click(screen.getByRole('button', { name: 'Set' }))
    const continueBtn = await screen.findByRole('button', { name: 'Continue' })

    fireEvent.click(continueBtn)

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(4))
    const lastCallBody = JSON.parse(fetch.mock.calls[2][1].body)
    expect(lastCallBody).toEqual({ access_level: 'admin', approval_request_id: 'abc' })
  })
})
