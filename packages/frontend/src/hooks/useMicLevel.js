import { useEffect, useRef } from 'react'

/**
 * Live microphone amplitude (0..1) while `active`, written into a ref - not state - so the
 * atom's 60fps animation loop can read it without a React re-render per audio frame.
 *
 * Fully local: Web Audio AnalyserNode over getUserMedia, no audio leaves the device (unlike
 * the SpeechRecognition wake-word path - see frontend/README.md's constraints section). If the
 * mic is denied or unavailable the level just stays 0 and the ring doesn't react - never an
 * error state.
 */
export function useMicLevel(active) {
  const levelRef = useRef(0)

  useEffect(() => {
    if (!active) {
      levelRef.current = 0
      return undefined
    }
    if (!navigator.mediaDevices?.getUserMedia) return undefined

    let stream
    let audioContext
    let rafId
    let cancelled = false

    const start = async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      } catch {
        return // mic denied/unavailable: level stays 0
      }
      if (cancelled) {
        stream.getTracks().forEach((t) => t.stop())
        return
      }
      audioContext = new (window.AudioContext || window.webkitAudioContext)()
      const source = audioContext.createMediaStreamSource(stream)
      const analyser = audioContext.createAnalyser()
      analyser.fftSize = 256
      source.connect(analyser)
      const samples = new Uint8Array(analyser.fftSize)

      const tick = () => {
        analyser.getByteTimeDomainData(samples)
        let sum = 0
        for (let i = 0; i < samples.length; i++) {
          const centered = (samples[i] - 128) / 128
          sum += centered * centered
        }
        // RMS is tiny for speech at normal distance; scale up and clamp so conversational
        // volume visibly moves the ring without shouting.
        levelRef.current = Math.min(Math.sqrt(sum / samples.length) * 4, 1)
        rafId = requestAnimationFrame(tick)
      }
      tick()
    }

    start()

    return () => {
      cancelled = true
      if (rafId) cancelAnimationFrame(rafId)
      if (stream) stream.getTracks().forEach((t) => t.stop())
      if (audioContext && audioContext.state !== 'closed') audioContext.close()
      levelRef.current = 0
    }
  }, [active])

  return levelRef
}
