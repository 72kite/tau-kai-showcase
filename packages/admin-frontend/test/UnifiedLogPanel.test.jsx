import { render, screen } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import UnifiedLogPanel from '../src/components/UnifiedLogPanel'

function jsonResponse(body, ok = true) {
  return { ok, status: ok ? 200 : 400, text: async () => JSON.stringify(body) }
}

describe('UnifiedLogPanel', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn())
  })
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('renders entries from the {history: [...]} shape /api/transcript/unified returns', async () => {
    fetch.mockResolvedValueOnce(
      jsonResponse({
        history: [
          { timestamp: '2026-09-12T10:00:00', speaker: 'user:zion', device_id: 'kids-tablet', text: 'hi tau' },
          { timestamp: '2026-09-12T10:00:02', speaker: 'tau', text: 'Hello.' },
        ],
      })
    )
    render(<UnifiedLogPanel token="tok" />)

    expect(await screen.findByText('hi tau')).toBeInTheDocument()
    expect(screen.getByText('Hello.')).toBeInTheDocument()
    expect(screen.getByText('Unified log (2)')).toBeInTheDocument()
  })

  it('shows an empty state with no history', async () => {
    fetch.mockResolvedValueOnce(jsonResponse({ history: [] }))
    render(<UnifiedLogPanel token="tok" />)

    expect(await screen.findByText('No conversation yet.')).toBeInTheDocument()
  })

  it('surfaces a fetch error without crashing', async () => {
    fetch.mockRejectedValueOnce(new Error('network blip'))
    render(<UnifiedLogPanel token="tok" />)

    expect(await screen.findByText('network blip')).toBeInTheDocument()
  })
})
