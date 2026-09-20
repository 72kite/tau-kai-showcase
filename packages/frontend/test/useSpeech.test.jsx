import { renderHook, act } from '@testing-library/react'
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { useSpeech } from '../src/hooks/useSpeech'

const MUTED_KEY = 'tau-voice-muted'

// Capture the buffer sources useSpeech creates so a test can fire their `onended` (the real
// "playback finished" event) on demand.
function installAudioContext() {
  const sources = []
  class MockAudioContext {
    constructor() {
      this.state = 'running'
      this.destination = {}
      this.resume = vi.fn().mockResolvedValue(undefined)
      this.decodeAudioData = vi.fn().mockResolvedValue({ duration: 1 })
      this.close = vi.fn()
    }
    createBufferSource() {
      const src = { buffer: null, connect: vi.fn(), start: vi.fn(), stop: vi.fn(), onended: null }
      sources.push(src)
      return src
    }
  }
  window.AudioContext = MockAudioContext
  delete window.webkitAudioContext
  return sources
}

function okSpeakResponse(audioB64 = 'AAAA') {
  return { ok: true, json: vi.fn().mockResolvedValue({ audio_base64: audioB64 }) }
}

describe('useSpeech onPlaybackEnd (Phase 17 arming signal)', () => {
  let sources

  beforeEach(() => {
    localStorage.clear()
    sources = installAudioContext()
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('fires onPlaybackEnd only when the reply audio actually finishes', async () => {
    const onPlaybackEnd = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(okSpeakResponse())
    const { result } = renderHook(() => useSpeech({ onPlaybackEnd }))

    await act(async () => {
      await result.current.speak('it is four o clock')
    })

    // Mid-playback: gate up, no follow-up armed yet.
    expect(result.current.speaking).toBe(true)
    expect(onPlaybackEnd).not.toHaveBeenCalled()

    // The audio ends -> now the follow-up window may open.
    act(() => sources[0].onended())
    expect(onPlaybackEnd).toHaveBeenCalledTimes(1)
    expect(result.current.speaking).toBe(false)
  })

  it('does NOT fire onPlaybackEnd when TTS is unavailable (503) - no phantom window', async () => {
    const onPlaybackEnd = vi.fn()
    global.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503, json: vi.fn() })
    const { result } = renderHook(() => useSpeech({ onPlaybackEnd }))

    await act(async () => {
      await result.current.speak('anything')
    })

    expect(global.fetch).toHaveBeenCalled()
    expect(onPlaybackEnd).not.toHaveBeenCalled()
    expect(result.current.speaking).toBe(false)
  })

  it('does NOT speak or arm when muted', async () => {
    localStorage.setItem(MUTED_KEY, '1')
    const onPlaybackEnd = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(okSpeakResponse())
    const { result } = renderHook(() => useSpeech({ onPlaybackEnd }))

    await act(async () => {
      await result.current.speak('should stay silent')
    })

    expect(global.fetch).not.toHaveBeenCalled()
    expect(onPlaybackEnd).not.toHaveBeenCalled()
    expect(result.current.speaking).toBe(false)
  })

  it('does NOT fire onPlaybackEnd on a manual stop (barge-in must not chain a follow-up)', async () => {
    const onPlaybackEnd = vi.fn()
    global.fetch = vi.fn().mockResolvedValue(okSpeakResponse())
    const { result } = renderHook(() => useSpeech({ onPlaybackEnd }))

    await act(async () => {
      await result.current.speak('a long reply being cut off')
    })
    expect(result.current.speaking).toBe(true)

    act(() => result.current.stop())
    expect(result.current.speaking).toBe(false)
    // Even if the source's onended arrives afterward, the live-source guard (sourceRef cleared by
    // stop()) means the follow-up callback is not invoked.
    act(() => sources[0].onended())
    expect(onPlaybackEnd).not.toHaveBeenCalled()
  })
})
