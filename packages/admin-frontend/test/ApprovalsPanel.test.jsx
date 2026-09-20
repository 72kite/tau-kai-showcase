import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import ApprovalsPanel from '../src/components/ApprovalsPanel'

function jsonResponse(body, ok = true) {
  return { ok, status: ok ? 200 : 400, text: async () => JSON.stringify(body) }
}

describe('ApprovalsPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders a pending approval with its reason and arguments', async () => {
    fetch.mockResolvedValueOnce(
      jsonResponse([
        {
          id: 'req1',
          server: 'home-assistant-mcp-server',
          tool: 'call_service',
          reason: 'turns on a physical device',
          arguments: { entity_id: 'light.kitchen', state: 'on' },
          requested_by: 'tau-core',
          requested_at: '2026-09-11T10:00:00',
        },
      ])
    )
    render(<ApprovalsPanel token="tok" />)

    expect(await screen.findByText('call_service')).toBeInTheDocument()
    expect(screen.getByText('@home-assistant-mcp-server')).toBeInTheDocument()
    expect(screen.getByText('turns on a physical device')).toBeInTheDocument()
    expect(screen.getByText('light.kitchen')).toBeInTheDocument()
    expect(screen.getByText('Pending approvals')).toBeInTheDocument()
    expect(screen.getByText('1')).toBeInTheDocument() // badge count
  })

  it('shows an empty state with nothing pending', async () => {
    fetch.mockResolvedValueOnce(jsonResponse([]))
    render(<ApprovalsPanel token="tok" />)

    expect(await screen.findByText('Nothing pending.')).toBeInTheDocument()
  })

  it('clicking Approve calls the approve endpoint and refreshes', async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse([{ id: 'req1', server: 's', tool: 't', requested_by: 'x' }]))
      .mockResolvedValueOnce(jsonResponse({ id: 'req1', status: 'approved' }))
      .mockResolvedValueOnce(jsonResponse([]))
    render(<ApprovalsPanel token="tok" />)
    await screen.findByText('t')

    fireEvent.click(screen.getByRole('button', { name: 'Approve' }))

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
    expect(fetch.mock.calls[1][0]).toContain('/api/approvals/req1/approve')
    expect(await screen.findByText('Nothing pending.')).toBeInTheDocument()
  })

  it('clicking Deny calls the deny endpoint', async () => {
    fetch
      .mockResolvedValueOnce(jsonResponse([{ id: 'req1', server: 's', tool: 't', requested_by: 'x' }]))
      .mockResolvedValueOnce(jsonResponse({ id: 'req1', status: 'denied' }))
      .mockResolvedValueOnce(jsonResponse([]))
    render(<ApprovalsPanel token="tok" />)
    await screen.findByText('t')

    fireEvent.click(screen.getByRole('button', { name: 'Deny' }))

    await waitFor(() => expect(fetch).toHaveBeenCalledTimes(3))
    expect(fetch.mock.calls[1][0]).toContain('/api/approvals/req1/deny')
  })

  it('surfaces a fetch error without crashing', async () => {
    fetch.mockRejectedValueOnce(new Error('network blip'))
    render(<ApprovalsPanel token="tok" />)

    expect(await screen.findByText('network blip')).toBeInTheDocument()
  })
})
