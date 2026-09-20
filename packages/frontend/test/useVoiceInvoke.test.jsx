import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useVoiceInvoke, FOLLOWUP_WINDOW_MS, FOLLOWUP_SETTLE_MS } from '../src/hooks/useVoiceInvoke'
import {
  installSpeechRecognition,
  uninstallSpeechRecognition,
  latestRecognition,
} from './mockSpeechRecognition'

// These tests exercise the DEFAULT voice mode ('browser') - the path where Phase 17's follow-up
// window carries real logic (an always-on recognizer whose wake requirement the window relaxes).
// Module-level VOICE_MODE is read at import; leaving VITE_VOICE_MODE unset keeps it 'browser'.

/** Render the hook with a fresh SpeechRecognition mock and a controllable speaking gate. */
function setup({ onCommand, bargeInEnabled = false, onBargeIn } = {}) {
  const speakingRef = { current: false }
  const cmd = onCommand || vi.fn().mockResolvedValue({ reply: 'ok' })
  // A realistic barge-in handler drops the speaking gate, exactly as useSpeech.stop() does.
  const barge = onBargeIn || vi.fn(() => { speakingRef.current = false })
  const utils = renderHook(() =>
    useVoiceInvoke({ onCommand: cmd, wakePhrase: 'hey tau', speakingRef, bargeInEnabled, onBargeIn: barge })
  )
  return { ...utils, speakingRef, onCommand: cmd, onBargeIn: barge }
}

/** Emit a recognition result inside act() so the resulting state updates flush. */
async function emit(transcript, opts) {
  await act(async () => {
    latestRecognition().emit(transcript, opts)
  })
}

describe('useVoiceInvoke (browser mode)', () => {
  beforeEach(() => {
    installSpeechRecognition()
    vi.useFakeTimers()
  })

  afterEach(() => {
    // Flush any still-armed window/capture timers inside act() so their state updates don't warn.
    act(() => vi.runOnlyPendingTimers())
    vi.useRealTimers()
    uninstallSpeechRecognition()
    vi.restoreAllMocks()
  })

  it('starts supported and idle, and begins listening', () => {
    const { result } = setup()
    expect(result.current.supported).toBe(true)
    expect(result.current.mode).toBe('idle')
    expect(latestRecognition().start).toHaveBeenCalled()
  })

  it('wake phrase with a trailing command invokes and dispatches the command', async () => {
    const { result, onCommand } = setup()
    await emit('hey tau turn on the lights')
    // handleCommand runs (thinking -> onCommand -> idle); with fake timers the awaited promise
    // resolves within the act flush above.
    expect(onCommand).toHaveBeenCalledWith('turn on the lights', null, 'voice')
    expect(result.current.mode).toBe('idle')
  })

  it('a bare final utterance in idle (no wake phrase) is ignored', async () => {
    const { result, onCommand } = setup()
    await emit('turn on the lights')
    expect(onCommand).not.toHaveBeenCalled()
    expect(result.current.mode).toBe('idle')
  })

  describe('follow-up window (Phase 17)', () => {
    it('armFollowup opens the window from idle', () => {
      const { result } = setup()
      act(() => result.current.armFollowup())
      expect(result.current.mode).toBe('followup')
    })

    it('exposes the configured window duration', () => {
      const { result } = setup()
      expect(result.current.followupWindowMs).toBe(FOLLOWUP_WINDOW_MS)
    })

    it('a BARE final utterance during the window continues the conversation (no wake phrase)', async () => {
      const { result, onCommand } = setup()
      act(() => result.current.armFollowup())
      act(() => vi.advanceTimersByTime(FOLLOWUP_SETTLE_MS + 10)) // past the echo-settle grace
      await emit('what about tomorrow')
      expect(onCommand).toHaveBeenCalledWith('what about tomorrow', null, 'voice')
      expect(result.current.mode).toBe('idle')
    })

    it('a final utterance heard right as the window opens is ignored (echo-settle grace)', async () => {
      // Regression test: this used to be Tau's own trailing room echo getting answered as a
      // "continuation," re-arming follow-up and looping (bug found 2026-09-08).
      const { result, onCommand } = setup()
      act(() => result.current.armFollowup())
      await emit('what about tomorrow') // no time advance - right at window-open
      expect(onCommand).not.toHaveBeenCalled()
      expect(result.current.mode).toBe('followup')
    })

    it('closes back to idle after the window elapses with no speech', () => {
      const { result, onCommand } = setup()
      act(() => result.current.armFollowup())
      expect(result.current.mode).toBe('followup')
      act(() => vi.advanceTimersByTime(FOLLOWUP_WINDOW_MS + 10))
      expect(result.current.mode).toBe('idle')
      expect(onCommand).not.toHaveBeenCalled()
    })

    it('an interim result re-arms the window so a slow start is not cut off', async () => {
      const { result } = setup()
      act(() => result.current.armFollowup())
      // Most of the way through the window...
      act(() => vi.advanceTimersByTime(FOLLOWUP_WINDOW_MS - 500))
      // ...the person starts talking (interim, not final): the window should reset, not close.
      await emit('hmm let me think', { isFinal: false })
      act(() => vi.advanceTimersByTime(FOLLOWUP_WINDOW_MS - 500))
      expect(result.current.mode).toBe('followup')
      // Only after a fresh full window of silence does it finally close.
      act(() => vi.advanceTimersByTime(1000))
      expect(result.current.mode).toBe('idle')
    })

    it('armFollowup is a no-op while a turn is in flight (not idle)', async () => {
      const { result } = setup()
      // Enter invoked via the atom-tap path.
      act(() => result.current.invoke())
      expect(result.current.mode).toBe('invoked')
      act(() => result.current.armFollowup())
      expect(result.current.mode).toBe('invoked')
    })
  })

  describe('half-duplex gate (Phase 16, regression)', () => {
    it('drops recognition results while Tau is speaking (barge-in off)', async () => {
      const { result, onCommand, speakingRef } = setup({ bargeInEnabled: false })
      speakingRef.current = true
      await emit('hey tau what time is it')
      expect(onCommand).not.toHaveBeenCalled()
      expect(result.current.mode).toBe('idle')
    })

    it('drops non-wake speech while speaking even with barge-in ON', async () => {
      const { result, onCommand, onBargeIn, speakingRef } = setup({ bargeInEnabled: true })
      speakingRef.current = true
      await emit('the weather looks nice today')
      expect(onBargeIn).not.toHaveBeenCalled()
      expect(onCommand).not.toHaveBeenCalled()
      expect(result.current.mode).toBe('idle')
    })
  })

  describe('barge-in (Phase 19)', () => {
    it('the wake phrase mid-reply interrupts: stops playback and captures the trailing command', async () => {
      const { result, onCommand, onBargeIn, speakingRef } = setup({ bargeInEnabled: true })
      speakingRef.current = true
      await emit('hey tau turn off the music')
      expect(onBargeIn).toHaveBeenCalledTimes(1) // playback stopped
      expect(onCommand).toHaveBeenCalledWith('turn off the music', null, 'voice')
    })

    it('the wake phrase alone mid-reply interrupts and opens capture (no trailing command yet)', async () => {
      const { result, onCommand, onBargeIn, speakingRef } = setup({ bargeInEnabled: true })
      speakingRef.current = true
      await emit('hey tau')
      expect(onBargeIn).toHaveBeenCalledTimes(1)
      expect(result.current.mode).toBe('invoked') // capture open, waiting for the command
      expect(onCommand).not.toHaveBeenCalled()
    })

    it('an interim (non-final) wake phrase does not barge in - waits for the final', async () => {
      const { result, onCommand, onBargeIn, speakingRef } = setup({ bargeInEnabled: true })
      speakingRef.current = true
      await emit('hey tau', { isFinal: false })
      expect(onBargeIn).not.toHaveBeenCalled()
      expect(onCommand).not.toHaveBeenCalled()
    })

    it('does not barge in when barge-in is disabled', async () => {
      const { onCommand, onBargeIn, speakingRef } = setup({ bargeInEnabled: false })
      speakingRef.current = true
      await emit('hey tau stop')
      expect(onBargeIn).not.toHaveBeenCalled()
      expect(onCommand).not.toHaveBeenCalled()
    })
  })

  describe('when SpeechRecognition is unavailable', () => {
    beforeEach(() => uninstallSpeechRecognition())

    it('reports unsupported and armFollowup never opens a window', () => {
      const { result } = setup()
      expect(result.current.supported).toBe(false)
      act(() => result.current.armFollowup())
      expect(result.current.mode).toBe('idle')
    })
  })
})
