import { useCallback, useEffect, useRef, useState } from 'react'
import { getApiBase } from './useMCPResource'
import { deviceHeaders } from '../utils/device'

/**
 * Phase 13 voice-out: read Tau's short reply aloud after a VOICE-initiated turn. POSTs the reply
 * text to /api/voice/speak (voice-mcp-server -> Piper) and plays the returned WAV via Web Audio.
 *
 * Best-effort by contract, mirroring the bridge endpoint: if TTS is unavailable (503, offline,
 * no Piper deployed) or the browser blocks playback, we log and stay silent - the text reply is
 * already on screen, so speaking failing is never a turn failing.
 *
 * `muted` is persisted per device (localStorage), like the theme toggle - a desk/kiosk can
 * silence voice-out without silencing anyone else's tablet.
 */

const MUTED_KEY = 'tau-voice-muted'

function b64ToArrayBuffer(b64) {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return bytes.buffer
}

export function useSpeech({ onPlaybackEnd } = {}) {
  const [muted, setMuted] = useState(() => localStorage.getItem(MUTED_KEY) === '1')
  const ctxRef = useRef(null)
  const sourceRef = useRef(null)
  // Fired only when a reply's audio actually finishes playing (not on TTS failure, mute, or a
  // manual stop). Phase 17's follow-up window arms off this: "Tau just finished speaking" is the
  // real signal, whereas the `speaking` flag also drops on a 503 where nothing was ever said.
  // Held in a ref so the long-lived source.onended closure never reads a stale callback.
  const onPlaybackEndRef = useRef(onPlaybackEnd)
  onPlaybackEndRef.current = onPlaybackEnd

  // Half-duplex gate (Phase 16 #1): true from the moment we commit to voicing a reply until that
  // audio ends (or fails). The voice-input hooks read this to suspend the mic while Tau speaks, so
  // Tau can't hear itself and re-trigger. `speaking` (state) drives the wake-word hook's `enabled`
  // prop; `speakingRef` (always-current) is read inside SpeechRecognition's long-lived callbacks,
  // which would otherwise close over a stale value.
  const [speaking, setSpeaking] = useState(false)
  const speakingRef = useRef(false)
  const setSpeakingBoth = useCallback((v) => {
    speakingRef.current = v
    setSpeaking(v)
  }, [])

  useEffect(() => {
    localStorage.setItem(MUTED_KEY, muted ? '1' : '0')
  }, [muted])

  // Stop the current source without touching the speaking flag - used inside speak() to cancel an
  // in-flight reply before starting the next one, while the gate stays up across the handover.
  const stopSource = useCallback(() => {
    try {
      sourceRef.current?.stop()
    } catch {
      // already ended - nothing to stop
    }
    sourceRef.current = null
  }, [])

  // Public stop (barge-in / manual): halt playback AND drop the gate, since after an explicit stop
  // Tau is no longer speaking and the mic should reopen.
  const stop = useCallback(() => {
    stopSource()
    setSpeakingBoth(false)
  }, [stopSource, setSpeakingBoth])

  const speak = useCallback(
    async (text) => {
      const trimmed = (text || '').trim()
      if (!trimmed || muted) return
      // Gate on from here: it must cover synthesis latency too, not just playback - during the
      // fetch the turn has already dropped back to idle, so the mic would otherwise be live.
      setSpeakingBoth(true)
      try {
        const res = await fetch(`${getApiBase()}/api/voice/speak`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...deviceHeaders() },
          body: JSON.stringify({ text: trimmed }),
        })
        if (!res.ok) {
          // 503 = TTS unavailable (no Piper / server down). Best-effort: stay silent.
          console.debug('[voice-out] speak unavailable:', res.status)
          setSpeakingBoth(false)
          return
        }
        const { audio_base64: audioB64 } = await res.json()
        if (!audioB64) {
          setSpeakingBoth(false)
          return
        }

        const ctx = ctxRef.current || new (window.AudioContext || window.webkitAudioContext)()
        ctxRef.current = ctx
        // A wake-word turn has no direct user gesture, so the context can be suspended by the
        // browser's autoplay policy; resume it (a no-op if already running).
        if (ctx.state === 'suspended') await ctx.resume()

        const buffer = await ctx.decodeAudioData(b64ToArrayBuffer(audioB64))
        stopSource() // never overlap two replies (keeps the gate up across the handover)
        const src = ctx.createBufferSource()
        src.buffer = buffer
        src.connect(ctx.destination)
        src.onended = () => {
          // Guard: a superseded source's stop() also fires onended, but sourceRef has already
          // moved on, so only the live source drops the gate.
          if (sourceRef.current === src) {
            sourceRef.current = null
            setSpeakingBoth(false)
            onPlaybackEndRef.current?.()
          }
        }
        src.start()
        sourceRef.current = src
      } catch (e) {
        // Autoplay block, decode failure, network - all non-fatal for a text turn.
        console.debug('[voice-out] playback failed:', e?.message)
        setSpeakingBoth(false)
      }
    },
    [muted, stopSource, setSpeakingBoth]
  )

  const toggleMuted = useCallback(() => setMuted((m) => !m), [])

  return { speak, stop, muted, toggleMuted, speaking, speakingRef }
}
