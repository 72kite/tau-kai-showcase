import { useCallback, useState } from 'react'
import { getApiBase } from './useMCPResource'
import { deviceHeaders } from '../utils/device'

/**
 * POSTs one user utterance to tau-core's /api/chat (tau_core/web/server.py) and returns the
 * assistant's reply. Both the wake-word flow and the atom-click flow funnel through this - it's
 * a thin wrapper around TauAssistant.chat(), so every tool call the model makes still goes
 * through TauCoreHost.call_tool and the CDG exactly as it would from any other caller. The
 * backend also mirrors both the request and the reply into ui-bridge-mcp-server's transcript
 * history, so the frontend doesn't need to touch local transcript state at all - the next poll
 * of ui://state picks the new lines up naturally.
 */
export function useChat() {
  const [error, setError] = useState(null)

  const send = useCallback(async (text, attachments = [], speaker = null) => {
    setError(null)
    try {
      const res = await fetch(`${getApiBase()}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...deviceHeaders() },
        body: JSON.stringify({ text, attachments, speaker }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `HTTP ${res.status}`)
      }
      return await res.json()
    } catch (e) {
      setError(e.message)
      throw e
    }
  }, [])

  return { send, error }
}
