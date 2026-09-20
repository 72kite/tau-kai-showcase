import { useEffect, useRef, useState } from 'react'
import { getApiBase } from './useMCPResource'
import { recordClip } from '../utils/recordClip'
import { isTauri } from '../utils/tauri'

// Native-exclusive (src-tauri/src/system.rs's keep_awake, SetThreadExecutionState): a browser tab
// has no way to stop the OS suspending the whole machine - the Screen Wake Lock API only keeps
// the *display* on, and doesn't survive a hidden/backgrounded window anyway. A suspended machine
// stops wake-word listening regardless of anything on the JS side, so this is the one part of
// "keep listening while minimized" that genuinely needs the desktop shell. No-op in the kiosk
// build and a no-op on non-Windows in the Rust command itself (see that file's own comment).
async function setKeepAwake(enabled) {
  if (!isTauri()) return
  try {
    const { invoke } = await import('@tauri-apps/api/core')
    await invoke('keep_awake', { enabled })
  } catch {
    // Best-effort - losing this never breaks listening, it just means the machine can sleep.
  }
}

// Fully-local hands-free wake word for VITE_VOICE_MODE=local. The browser SpeechRecognition
// path (useVoiceInvoke, 'browser' mode) gives "hey tau" for free but streams audio to Chrome's
// cloud recognizer; local mode had no wake word at all (double-tap only). This closes that gap:
// it records short rolling windows and posts each to /api/voice/wake -> voice-mcp-server
// detect_wake_word -> openWakeWord, on-device. Audio never leaves the LAN.
//
// NOTE: voice-mcp-server now ships a locally-trained "hey tau" model (models/hey_tau.onnx) and
// uses it by default, so local mode finally listens for the real phrase rather than "hey jarvis".
// Its recall is modest though (~36% at the default threshold - see that package's README for the
// measured curve), so double-tap remains the reliable path and this is a convenience. This hook
// doesn't care which phrase; it only reacts to `detected`.
const VOICE_MODE = (import.meta.env.VITE_VOICE_MODE || 'local').toLowerCase()

// Window length per detection poll. Long enough to contain a short wake phrase, short enough to
// keep latency low. A fresh getUserMedia per window (via recordClip) is simple and reliable;
// the small gap between windows just means an occasional "say it again."
const WINDOW_SECONDS = Number(import.meta.env.VITE_WAKEWORD_WINDOW_SECONDS || 1.3)
// Backoff after a failed poll so a server/model outage doesn't spin the mic in a tight loop.
const ERROR_BACKOFF_MS = 3000
// Pause after a successful detection so we don't re-fire on the tail of the same phrase.
const POST_DETECT_PAUSE_MS = 1500

/**
 * Always-on local wake-word listener. Active only when `enabled` (the caller should pass
 * `mode === 'idle'` so it yields the mic during command capture and TTS playback) and only in
 * local voice mode. Calls `onWake()` when the model fires; wire that to the same invoke() the
 * atom double-tap uses.
 */
export function useWakeWord({ onWake, enabled = true } = {}) {
  const [status, setStatus] = useState('idle') // 'idle' | 'listening' | 'error:*' | 'disabled'
  const onWakeRef = useRef(onWake)
  onWakeRef.current = onWake

  useEffect(() => {
    if (VOICE_MODE !== 'local') {
      setStatus('disabled') // 'browser' mode gets its wake phrase from SpeechRecognition instead
      return undefined
    }
    if (!enabled) {
      setStatus('idle')
      return undefined
    }
    if (!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)) {
      setStatus('unsupported')
      return undefined
    }

    let cancelled = false
    setStatus('listening')
    setKeepAwake(true)

    const loop = async () => {
      while (!cancelled) {
        try {
          const { data_b64, filename } = await recordClip(WINDOW_SECONDS)
          if (cancelled) return
          const res = await fetch(`${getApiBase()}/api/voice/wake`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ audio_b64: data_b64, filename }),
          })
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const { detected } = await res.json()
          if (cancelled) return
          if (detected) {
            onWakeRef.current?.()
            // Yield the mic; the caller flips `enabled` off while capturing, which unmounts this
            // loop. If it doesn't, this pause still avoids an immediate double-fire.
            await new Promise((r) => setTimeout(r, POST_DETECT_PAUSE_MS))
          }
        } catch (e) {
          if (cancelled) return
          setStatus(`error:${e.message}`)
          await new Promise((r) => setTimeout(r, ERROR_BACKOFF_MS))
          if (!cancelled) setStatus('listening')
        }
      }
    }
    loop()

    return () => {
      cancelled = true
      setKeepAwake(false)
    }
  }, [enabled])

  return { status }
}
