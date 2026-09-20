import { useCallback, useEffect, useRef, useState } from 'react'
import { getApiBase } from './useMCPResource'
import { MIC_AUDIO_CONSTRAINTS } from '../utils/audioConstraints'
import { containsSleepPhrase, findWakePhrase } from '../utils/voiceMatch'

const DEFAULT_WAKE_PHRASE = (import.meta.env.VITE_WAKE_PHRASE || 'hey tau').toLowerCase()

// 'browser' = SpeechRecognition (wake phrase works, but Chrome ships audio to Google's cloud
// recognizer). 'local' = MediaRecorder capture -> POST /api/voice/transcribe -> the local
// faster-whisper service (infra/whisper) - fully offline. Local hands-free wake word is handled
// separately by useWakeWord (openWakeWord via /api/voice/wake); the atom double-tap also invokes.
// This raw fallback stays 'browser' (test/useVoiceInvoke.test.jsx relies on it) - the actual
// deployed default is 'local' via .env.example's VITE_VOICE_MODE, not this in-code fallback.
const VOICE_MODE = (import.meta.env.VITE_VOICE_MODE || 'browser').toLowerCase()

// Local-capture VAD tuning: RMS above SPEECH_RMS counts as speech; recording stops
// SILENCE_AFTER_SPEECH_MS after the last speech frame (generous, so "umm" pauses survive).
const SPEECH_RMS = 0.015

// Auto-stop tuning. NO_SPEECH: invoked but nothing heard at all -> quietly return to idle.
// SILENCE_AFTER_SPEECH: pause length after the last heard fragment before we take what we have -
// deliberately generous (3s) so natural thinking pauses and fillers ("umm...") don't cut the
// speaker off; interim recognition results keep arriving through fillers and reset this timer.
// MAX_CAPTURE: hard ceiling on one capture - submit whatever was heard, or give up.
const NO_SPEECH_TIMEOUT_MS = 8000
const SILENCE_AFTER_SPEECH_MS = 3000
const MAX_CAPTURE_MS = 25000

// Bounded conversational follow-up (Phase 17). After Tau finishes SPEAKING a voice reply, a short
// window opens in which a BARE utterance - no wake phrase - is accepted as a continuation, then it
// times out back to idle. Bounded on purpose: false triggers can't run away, because silence just
// closes the window. Off via VITE_FOLLOWUP_ENABLED=false; duration via VITE_FOLLOWUP_WINDOW_MS.
export const FOLLOWUP_ENABLED =
  String(import.meta.env.VITE_FOLLOWUP_ENABLED ?? 'true').toLowerCase() !== 'false'
export const FOLLOWUP_WINDOW_MS = Number(import.meta.env.VITE_FOLLOWUP_WINDOW_MS || 7000)
// Echo-settle grace period (bug found 2026-09-08): the window opens the instant Tau's TTS
// AudioBufferSourceNode fires `onended`, which is when playback finishes on the Web Audio clock -
// not when the room actually goes quiet. On kiosk hardware with one mic/one speaker and no real
// AEC (same hardware limitation that made barge-in default off, see App.jsx's BARGE_IN_DEFAULT),
// the tail/room-echo of Tau's own voice was getting caught as the "bare utterance continuation"
// this window accepts with NO wake phrase required, submitted as a new command, and answered -
// which re-arms follow-up and repeats: a live user hit this as "tau picks up itself and responds
// to itself in a loop." Unlike barge-in, this path isn't gated by BARGE_IN_DEFAULT at all, so that
// fix never covered it. Fix: ignore speech/transcripts for this long after the window opens,
// giving echo time to decay, before either capture path (local VAD or browser SpeechRecognition)
// starts treating what it hears as real.
export const FOLLOWUP_SETTLE_MS = Number(import.meta.env.VITE_FOLLOWUP_SETTLE_MS || 700)

// Explicit closer (Phase 16 next slice): a sleep phrase (see utils/voiceMatch.js) puts the wake
// phrase to sleep for SLEEP_MS - a deliberate "stop listening", not just letting the current
// capture time out.
const SLEEP_MS = 45000

function getSpeechRecognitionCtor() {
  return window.SpeechRecognition || window.webkitSpeechRecognition || null
}

/**
 * Unified voice-invoke state machine for the atom. Passively listens for a wake phrase via the
 * browser's SpeechRecognition API; once triggered - by the wake phrase OR a manual invoke()
 * call from the atom's onClick - captures the next spoken utterance as a command and hands it
 * to onCommand.
 *
 * Browser support for continuous SpeechRecognition varies a lot, and notably Safari on older
 * iOS (the primary kiosk-tablet target - see project-tau-plan.md Phase 3) has historically had
 * weak or no support. When unsupported, `supported` is false; the caller should fall back to
 * `submitCommand(text)` from a text input rather than relying on `invoke()` to ever produce a
 * command on its own.
 *
 * Also requires a secure context (HTTPS or localhost) for microphone access in most browsers -
 * a plain-HTTP LAN kiosk deployment will not get wake-word/voice capture at all. See the
 * "Known gaps" section of the root README.
 */
export function useVoiceInvoke({
  onCommand,
  wakePhrase = DEFAULT_WAKE_PHRASE,
  speakingRef,
  // Stops Tau's own playback - used both by a manual invoke() (atom tap / local wake word) while
  // Tau is mid-reply, and as the default barge-in action below.
  stopSpeaking,
  // Phase 19 barge-in: when true, the wake phrase heard WHILE Tau is speaking interrupts the reply
  // (onBargeIn stops playback) and opens capture, instead of being dropped by the half-duplex gate.
  bargeInEnabled = false,
  onBargeIn,
} = {}) {
  const [mode, setMode] = useState('idle') // 'idle' | 'invoked' | 'thinking' | 'followup' | 'sleeping'
  const [supported, setSupported] = useState(false)
  const [error, setError] = useState(null)
  // Last recognition lifecycle event, for the status-bar MIC indicator - voice problems are
  // otherwise invisible without the browser console open (e.g. mic permission denied, or
  // Chrome's cloud recognizer unreachable).
  const [voiceStatus, setVoiceStatus] = useState('init')
  const [followUpSecondsLeft, setFollowUpSecondsLeft] = useState(null)

  // Mirrors `mode` for use inside SpeechRecognition's event callbacks, which close over stale
  // state otherwise (the callbacks are registered once per recognition instance, not per render).
  const modeRef = useRef('idle')
  const stoppedRef = useRef(false)
  // Capture bookkeeping for auto-stop: the freshest interim transcript this capture, and the
  // silence timer that fires SILENCE_AFTER_SPEECH_MS after the last heard fragment.
  const interimRef = useRef('')
  const silenceTimerRef = useRef(null)
  // Follow-up window timer (Phase 17): fires FOLLOWUP_WINDOW_MS after the window opens (and after
  // each heard fragment), closing it back to idle if no continuation was spoken. Held in a ref so
  // both the recognition callbacks and armFollowup() can clear it.
  const followupTimerRef = useRef(null)
  // When the current follow-up window actually opened (armFollowup) - the echo-settle anchor for
  // FOLLOWUP_SETTLE_MS above. 0 outside a follow-up window.
  const followupArmedAtRef = useRef(0)
  // Mirrors `supported` for armFollowup(), which is called from an effect after playback ends and
  // shouldn't need to be re-created every time `supported` settles.
  const supportedRef = useRef(false)
  // Barge-in (Phase 19) read inside the long-lived recognition callback, which is registered once
  // and would otherwise close over the initial prop values.
  const bargeInRef = useRef(bargeInEnabled)
  bargeInRef.current = bargeInEnabled
  const onBargeInRef = useRef(onBargeIn)
  onBargeInRef.current = onBargeIn

  const setModeBoth = useCallback((next) => {
    modeRef.current = next
    setMode(next)
  }, [])

  const clearFollowupTimer = useCallback(() => {
    if (followupTimerRef.current) {
      clearTimeout(followupTimerRef.current)
      followupTimerRef.current = null
    }
  }, [])

  // Visual-only countdown for the follow-up window (drives the on-screen "still listening (Ns)"
  // indicator via ModelActivity); the actual timeout that reverts to idle lives in the capture
  // effects below and in armFollowup/clearFollowupTimer.
  useEffect(() => {
    if (mode !== 'followup') {
      setFollowUpSecondsLeft(null)
      return undefined
    }
    setFollowUpSecondsLeft(Math.ceil(FOLLOWUP_WINDOW_MS / 1000))
    const startedAt = Date.now()
    const id = setInterval(() => {
      const left = Math.max(0, Math.ceil((FOLLOWUP_WINDOW_MS - (Date.now() - startedAt)) / 1000))
      setFollowUpSecondsLeft(left)
    }, 250)
    return () => clearInterval(id)
  }, [mode])

  // Explicit closer (stop-phrase): 'sleeping' ignores the wake phrase entirely until SLEEP_MS
  // passes, then wakes back up to 'idle' on its own. An atom tap can also end it early (see
  // `invoke` below) - the phrase only suppresses the *passive* wake word, never manual control.
  useEffect(() => {
    if (mode !== 'sleeping') return undefined
    const id = setTimeout(() => {
      if (modeRef.current === 'sleeping') setModeBoth('idle')
    }, SLEEP_MS)
    return () => clearTimeout(id)
  }, [mode, setModeBoth])

  const handleCommand = useCallback(
    // `source` tells the caller whether this turn arrived by voice or by typed text. Voice-out
    // (Phase 13) reads replies aloud only for voice-initiated turns, so a typed question stays
    // silent; every internal (voice) caller uses the default, and the text-input fallback goes
    // through `submitCommand` below, which tags 'text'.
    async (text, speaker = null, source = 'voice') => {
      const trimmed = (text || '').trim()
      if (!trimmed) {
        setModeBoth('idle')
        return
      }
      setModeBoth('thinking')
      try {
        await onCommand?.(trimmed, speaker, source)
      } finally {
        setModeBoth('idle')
      }
    },
    [onCommand, setModeBoth]
  )

  // The text-input fallback (unsupported-browser manual form, and the attach-a-file path) submits
  // through here, so those turns are tagged 'text' and never spoken back.
  const submitCommand = useCallback(
    (text) => handleCommand(text, null, 'text'),
    [handleCommand]
  )

  useEffect(() => {
    // Mic capture (SpeechRecognition AND getUserMedia) requires a SECURE CONTEXT - HTTPS or
    // localhost. Over plain http:// to a LAN IP (a phone hitting http://<host>:3000), the browser
    // refuses the mic WITHOUT ever prompting; iOS Safari surfaces that as an 'not-allowed' error,
    // which the UI used to render as a misleading "microphone blocked - allow access" (there was
    // no prompt to allow). Detect it up front and fall back to the text input with an honest
    // reason, instead of attempting capture and showing a blocked-mic error nobody can act on.
    if (typeof window !== 'undefined' && window.isSecureContext === false) {
      setSupported(false)
      setVoiceStatus('insecure')
      return undefined
    }
    if (VOICE_MODE === 'local') {
      // Local mode: no SpeechRecognition at all - capture happens in the dedicated effect
      // below. "Supported" means we can record; transcription runs server-side.
      const ok = !!(navigator.mediaDevices?.getUserMedia && window.MediaRecorder)
      setSupported(ok)
      supportedRef.current = ok
      setVoiceStatus(ok ? 'local-ready' : 'unsupported')
      return undefined
    }
    const SpeechRecognitionCtor = getSpeechRecognitionCtor()
    if (!SpeechRecognitionCtor) {
      setSupported(false)
      setVoiceStatus('unsupported')
      return undefined
    }
    setSupported(true)
    supportedRef.current = true
    stoppedRef.current = false

    const recognition = new SpeechRecognitionCtor()
    recognition.continuous = true
    recognition.interimResults = true
    recognition.lang = 'en-US'

    const clearSilenceTimer = () => {
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current)
        silenceTimerRef.current = null
      }
    }

    recognition.onstart = () => {
      setVoiceStatus('listening')
      console.debug('[voice] recognition started')
    }

    recognition.onresult = (event) => {
      const result = event.results[event.results.length - 1]
      const transcript = result[0].transcript.trim()

      // Half-duplex gate (Phase 16 #1): while Tau is voicing a reply, Chrome's mic (no
      // echoCancellation) picks up its own TTS, so we drop what we hear to stop it re-triggering.
      // Phase 19 barge-in carves out ONE exception: a FINAL wake phrase heard mid-reply is the
      // user deliberately cutting in - stop playback (onBargeIn drops the speaking gate) and open
      // capture for whatever follows the phrase. Only the full wake phrase does this; ordinary
      // words stay dropped - but this still assumes Tau's own reply never literally says "hey
      // tau" (e.g. explaining its own wake phrase), which isn't guaranteed either. Combined with
      // local mode's AEC-reliance issue (App.jsx's BARGE_IN_DEFAULT), this is why barge-in now
      // defaults off. Uses the same
      // findWakePhrase matcher as the passive path below, not a raw substring check, so barge-in
      // and normal wake detection can't silently disagree on what counts as a match.
      if (speakingRef?.current) {
        if (!bargeInRef.current || !result.isFinal || !transcript) return
        const idx = findWakePhrase(transcript, wakePhrase)
        if (idx === -1) return
        console.debug('[voice] barge-in: wake phrase heard during playback')
        onBargeInRef.current?.()
        const trailing = transcript.slice(idx + wakePhrase.length).trim()
        setModeBoth('invoked')
        if (trailing) handleCommand(trailing)
        return
      }

      setVoiceStatus(result.isFinal ? 'heard-final' : 'hearing')
      console.debug(`[voice] ${result.isFinal ? 'FINAL' : 'interim'} (mode=${modeRef.current}):`, transcript)

      if (modeRef.current === 'invoked') {
        // Any heard fragment - interim or final - counts as activity: remember it and push
        // the silence deadline out. Fillers ("umm") produce interim results too, so a
        // speaker pausing to think never gets cut off mid-thought, only true silence does.
        // 'followup' is NOT included here - its timing is owned entirely by the dedicated
        // FOLLOWUP_WINDOW_MS timer below; racing this shorter SILENCE_AFTER_SPEECH_MS timer
        // against it would submit a follow-up capture too early.
        if (transcript) interimRef.current = transcript
        clearSilenceTimer()
        silenceTimerRef.current = setTimeout(() => {
          const heard = interimRef.current
          interimRef.current = ''
          if (modeRef.current !== 'invoked') return
          if (heard) handleCommand(heard)
          else setModeBoth('idle')
        }, SILENCE_AFTER_SPEECH_MS)
      }

      if (modeRef.current === 'followup') {
        // Echo-settle grace (see FOLLOWUP_SETTLE_MS): ignore everything heard right as the window
        // opens - that's Tau's own trailing room echo, not the person talking. Doesn't reset or
        // extend the window; it just doesn't count as activity yet.
        if (Date.now() - followupArmedAtRef.current < FOLLOWUP_SETTLE_MS) return
        // Bounded follow-up window (Phase 17): a bare utterance continues the conversation without
        // the wake phrase. Any heard speech keeps the window open (so a slow start isn't cut off);
        // a FINAL utterance promotes into normal command capture and returns us to idle otherwise.
        if (transcript) {
          clearFollowupTimer()
          followupTimerRef.current = setTimeout(() => {
            followupTimerRef.current = null
            if (modeRef.current === 'followup') setModeBoth('idle')
          }, FOLLOWUP_WINDOW_MS)
        }
        if (result.isFinal && transcript) {
          clearFollowupTimer()
          setModeBoth('invoked')
          handleCommand(transcript)
        }
        return
      }

      if (!result.isFinal) return

      // Explicit closer: a stop-phrase heard while idle, actively listening for a command, or in
      // the follow-up window puts the wake phrase to sleep instead of doing anything else with
      // the transcript - including not submitting an in-progress capture as a command.
      if (
        (modeRef.current === 'idle' || modeRef.current === 'invoked' || modeRef.current === 'followup') &&
        containsSleepPhrase(transcript)
      ) {
        console.debug('[voice] stop-phrase heard, sleeping the wake word')
        clearSilenceTimer()
        interimRef.current = ''
        setModeBoth('sleeping')
        return
      }

      if (modeRef.current === 'idle') {
        const idx = findWakePhrase(transcript, wakePhrase)
        if (idx === -1) return
        const trailing = transcript.slice(idx + wakePhrase.length).trim()
        setModeBoth('invoked')
        if (trailing) handleCommand(trailing)
        return
      }

      if (modeRef.current === 'invoked') {
        // (mode is never 'followup' here - that case returns earlier, above.)
        clearSilenceTimer()
        interimRef.current = ''
        handleCommand(transcript)
      }
      // while 'thinking'/'sleeping': ignore further speech until back to idle, so a reply being
      // read aloud (or background noise) can't be picked up as the start of a new command.
    }

    recognition.onerror = (event) => {
      console.debug('[voice] error:', event.error)
      // "no-speech"/"aborted" are routine (silence timeouts, our own restart-on-end below) -
      // only surface anything else, and never let an error kill the passive listening loop.
      if (event.error !== 'no-speech' && event.error !== 'aborted') {
        setVoiceStatus(`error:${event.error}`)
        setError(
          event.error === 'not-allowed' || event.error === 'service-not-allowed'
            ? 'microphone blocked - allow mic access for this site (lock icon in the address bar), then reload'
            : event.error === 'network'
              ? "speech service unreachable - Chrome's recognizer needs internet access"
              : event.error
        )
      }
    }

    recognition.onend = () => {
      console.debug('[voice] recognition ended (restarting:', !stoppedRef.current, ')')
      if (stoppedRef.current) return
      // Browsers stop continuous recognition after a period of silence; restart to keep the
      // wake-word listener effectively always-on.
      try {
        recognition.start()
      } catch {
        // Already starting, or a transient failure - the next onend will retry.
      }
    }

    try {
      recognition.start()
    } catch (e) {
      setError(e.message)
    }

    return () => {
      stoppedRef.current = true
      recognition.onend = null
      recognition.onresult = null
      recognition.stop()
      clearFollowupTimer()
    }
    // speakingRef is a stable ref (identity never changes), read inside onresult; listed for
    // clarity, it does not cause the listener to re-register.
  }, [wakePhrase, handleCommand, setModeBoth, speakingRef, clearFollowupTimer])

  // Local capture engine: on invoke, record with MediaRecorder while watching RMS for voice
  // activity; stop SILENCE_AFTER_SPEECH_MS after the last speech frame (fillers and thinking
  // pauses keep RMS above threshold often enough to survive), or at MAX_CAPTURE_MS; give up
  // after NO_SPEECH_TIMEOUT_MS if nothing was ever said. The finished clip goes to
  // /api/voice/transcribe (voice-mcp-server -> local faster-whisper) - audio never leaves
  // the LAN.
  useEffect(() => {
    if (VOICE_MODE !== 'local' || (mode !== 'invoked' && mode !== 'followup') || !supported) {
      return undefined
    }
    // A follow-up capture (Phase 17) is an ordinary local capture with a shorter no-speech leash:
    // if the person doesn't take the opening within the follow-up window, we quietly return to idle
    // (which re-arms the wake word) instead of waiting out the full NO_SPEECH_TIMEOUT_MS.
    const noSpeechMs = mode === 'followup' ? FOLLOWUP_WINDOW_MS : NO_SPEECH_TIMEOUT_MS

    let cancelled = false
    let stream
    let audioContext
    let recorder
    let pollTimer
    const chunks = []
    const startedAt = Date.now()
    let lastSpeechAt = 0

    const cleanup = () => {
      if (pollTimer) clearInterval(pollTimer)
      if (recorder && recorder.state !== 'inactive') recorder.stop()
      if (stream) stream.getTracks().forEach((t) => t.stop())
      if (audioContext && audioContext.state !== 'closed') audioContext.close()
    }

    const finishAndTranscribe = async () => {
      if (cancelled) return
      clearInterval(pollTimer)
      setVoiceStatus('transcribing')
      const mimeType = recorder.mimeType || 'audio/webm'
      await new Promise((resolve) => {
        recorder.onstop = resolve
        recorder.stop()
      })
      stream.getTracks().forEach((t) => t.stop())
      if (audioContext.state !== 'closed') audioContext.close()

      const blob = new Blob(chunks, { type: mimeType })
      const dataUrl = await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = () => resolve(String(reader.result))
        reader.onerror = () => reject(reader.error)
        reader.readAsDataURL(blob)
      })
      const audio_b64 = dataUrl.slice(dataUrl.indexOf(',') + 1)

      try {
        const res = await fetch(`${getApiBase()}/api/voice/transcribe`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            audio_b64,
            filename: mimeType.includes('mp4') ? 'utterance.mp4' : 'utterance.webm',
          }),
        })
        if (!res.ok) {
          const body = await res.json().catch(() => ({}))
          throw new Error(body.detail || `HTTP ${res.status}`)
        }
        const { text, speaker } = await res.json()
        if (cancelled) return
        setVoiceStatus(text ? 'heard-final' : 'listening')
        console.debug('[voice local] transcript:', text, 'speaker:', speaker)
        // Explicit closer: a stop-phrase in the capture sleeps the wake word instead of being
        // submitted as a command - same behavior as the browser-mode path, applied here since
        // local mode only sees a transcript after the fact, not word-by-word.
        if (text && containsSleepPhrase(text)) {
          setModeBoth('sleeping')
          return
        }
        // speaker is the voiceprint-identified person (or null = unknown); rides along so the
        // shared transcript can show WHO asked.
        if (text && text.trim()) handleCommand(text, speaker?.person_id ?? null)
        else setModeBoth('idle')
      } catch (e) {
        if (cancelled) return
        setVoiceStatus('error:transcribe')
        setError(`local transcription failed: ${e.message}`)
        setModeBoth('idle')
      }
    }

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: MIC_AUDIO_CONSTRAINTS })
      } catch {
        setVoiceStatus('error:not-allowed')
        setError('microphone blocked - allow mic access for this site, then reload')
        setModeBoth('idle')
        return
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      audioContext = new (window.AudioContext || window.webkitAudioContext)()
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      audioContext.createMediaStreamSource(stream).connect(analyser)
      const samples = new Uint8Array(analyser.fftSize)

      recorder = new MediaRecorder(stream)
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunks.push(e.data)
      }
      recorder.start(250)
      setVoiceStatus('recording')

      pollTimer = setInterval(() => {
        analyser.getByteTimeDomainData(samples)
        let sum = 0
        for (let i = 0; i < samples.length; i++) {
          const centered = (samples[i] - 128) / 128
          sum += centered * centered
        }
        const rms = Math.sqrt(sum / samples.length)
        const now = Date.now()
        // Echo-settle grace (see FOLLOWUP_SETTLE_MS): a follow-up capture opens the mic the
        // instant Tau's own TTS finishes, so the first stretch of "speech" it hears is that
        // reply's own room echo, not the person. Don't count it toward lastSpeechAt - the capture
        // still auto-stops on real MAX_CAPTURE_MS/noSpeechMs if nothing follows.
        const inEchoGrace = mode === 'followup' && now - startedAt < FOLLOWUP_SETTLE_MS
        if (rms > SPEECH_RMS && !inEchoGrace) lastSpeechAt = now

        const elapsed = now - startedAt
        if (!lastSpeechAt && elapsed > noSpeechMs) {
          cleanup()
          setVoiceStatus('listening')
          setModeBoth('idle')
          return
        }
        if (
          (lastSpeechAt && now - lastSpeechAt > SILENCE_AFTER_SPEECH_MS) ||
          elapsed > MAX_CAPTURE_MS
        ) {
          finishAndTranscribe()
        }
      }, 100)
    }

    start()

    return () => {
      cancelled = true
      cleanup()
    }
  }, [mode, supported, handleCommand, setModeBoth])

  // Auto-stop: arm per-capture timers whenever listening starts. Voice-capable browsers only -
  // the unsupported-browser fallback is a text input, and closing it under someone mid-typing
  // would be worse than leaving it open. (Browser-recognition mode only: local mode does its
  // own VAD-based auto-stop above.) 'invoked' only, deliberately - 'followup' has its own
  // dedicated FOLLOWUP_WINDOW_MS timer in the onresult handler above, and is NOT included here;
  // racing this NO_SPEECH_TIMEOUT_MS/MAX_CAPTURE_MS pair against that timer for the same mode
  // would let whichever fires first cut the follow-up window short.
  useEffect(() => {
    if (VOICE_MODE === 'local' || mode !== 'invoked' || !supported) return undefined
    interimRef.current = ''

    const noSpeechTimer = setTimeout(() => {
      if (!interimRef.current && modeRef.current === 'invoked') setModeBoth('idle')
    }, NO_SPEECH_TIMEOUT_MS)

    const hardCapTimer = setTimeout(() => {
      if (modeRef.current !== 'invoked') return
      const heard = interimRef.current
      interimRef.current = ''
      if (heard) handleCommand(heard)
      else setModeBoth('idle')
    }, MAX_CAPTURE_MS)

    return () => {
      clearTimeout(noSpeechTimer)
      clearTimeout(hardCapTimer)
      if (silenceTimerRef.current) {
        clearTimeout(silenceTimerRef.current)
        silenceTimerRef.current = null
      }
    }
  }, [mode, supported, handleCommand, setModeBoth])

  const invoke = useCallback(() => {
    // 'idle' or 'sleeping' both accept an explicit invoke - a stop-phrase only suppresses the
    // *passive* wake word, never a deliberate tap, so it doubles as an early wake-up.
    if (modeRef.current !== 'idle' && modeRef.current !== 'sleeping') return
    // Explicit invoke (atom tap, or a local-mode wake-word onWake) always barges in over
    // playback - mode reads 'idle' while TTS plays (see useSpeech), so without this an atom tap
    // mid-reply would open capture without ever stopping Tau's own voice.
    if (speakingRef?.current) stopSpeaking?.()
    setModeBoth('invoked')
  }, [setModeBoth, speakingRef, stopSpeaking])

  // Explicit closer: tap-anywhere-to-stop. Cancels an in-progress capture (invoked/followup)
  // without submitting whatever was heard so far - distinct from the silence timers, which take
  // whatever was heard. A no-op from any other mode.
  const cancel = useCallback(() => {
    if (modeRef.current !== 'invoked' && modeRef.current !== 'followup') return
    interimRef.current = ''
    setModeBoth('idle')
  }, [setModeBoth])

  // Open the bounded follow-up window (Phase 17). Called by App the moment Tau finishes SPEAKING a
  // voice reply, so a bare continuation ("and the garage?") lands without another wake phrase. A
  // no-op unless we're at rest (idle) and voice-capable - a text turn, or a turn still in flight,
  // never opens one. Browser mode relaxes the wake requirement in place; local mode opens a capture
  // (the local effect above keys on 'followup' too). Silence closes the window back to idle.
  const armFollowup = useCallback(() => {
    if (!FOLLOWUP_ENABLED) return
    if (!supportedRef.current) return
    if (modeRef.current !== 'idle') return
    followupArmedAtRef.current = Date.now()
    setModeBoth('followup')
    clearFollowupTimer()
    followupTimerRef.current = setTimeout(() => {
      followupTimerRef.current = null
      if (modeRef.current === 'followup') setModeBoth('idle')
    }, FOLLOWUP_WINDOW_MS)
  }, [setModeBoth, clearFollowupTimer])

  return {
    mode,
    supported,
    error,
    voiceStatus,
    invoke,
    cancel,
    submitCommand,
    armFollowup,
    followupWindowMs: FOLLOWUP_WINDOW_MS,
    followUpSecondsLeft,
  }
}
