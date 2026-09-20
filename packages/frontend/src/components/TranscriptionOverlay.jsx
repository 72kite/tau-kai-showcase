import React, { useEffect, useMemo, useRef, useState } from 'react'
import NeuronField from './NeuronField'
import './TranscriptionOverlay.css'

/**
 * The response surface (Phase 6.F). Replaces the scrolling chat log that used to live in its own
 * row below the atom: it now sits *beside* the atom inside the atom's own box, and shows only the
 * latest exchange - what Tau heard, and what Tau said - as a single flowing karaoke/lyric line.
 *
 * Why only the latest turn: per 6.D, the durable cross-device record lives admin-side in the
 * unified log, not on a kiosk screen anyone can walk up to and scroll back through. The surface
 * auto-hides ~30s after the reply (App.jsx owns that timer) and the user can bring it back by
 * tapping the atom's box.
 *
 * While Tau is still working (`working`, i.e. a turn in flight with no reply yet), this space shows
 * the NeuronField instead - the live picture of which servers are being called (6.C second pass).
 * The reply displaces it the moment it lands.
 *
 * Karaoke timing is a reading-cadence approximation, NOT real TTS timing: the bridge doesn't
 * publish word/phoneme timestamps from Piper, so there is nothing to sync to. Words advance on a
 * length-proportional interval, which reads right but will drift against actual audio on a long
 * reply. Wiring real timings would need a Piper alignment channel through voice-mcp-server.
 * `prefers-reduced-motion` skips the animation and reveals the whole line at once.
 */

// Base dwell per word plus a per-character component - long words hold longer, which tracks
// speech better than a flat interval.
const WORD_BASE_MS = 150
const WORD_PER_CHAR_MS = 28

function prefersReducedMotion() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  )
}

/**
 * Walk the history backwards to pull out the current turn: the newest Tau reply, the memory-recall
 * note that fed it, and the user utterance that prompted it. Returns nulls for whatever hasn't
 * happened yet (e.g. mid-turn, there's a `heard` but no `reply`).
 */
export function latestExchange(history) {
  let reply = null
  let heard = null
  let memory = null
  for (let i = history.length - 1; i >= 0; i--) {
    const entry = history[i]
    const speaker = String(entry.speaker || '')
    if (speaker.startsWith('user')) {
      heard = entry
      break
    }
    if (speaker === 'memory') {
      if (!memory) memory = entry
      continue
    }
    if (!reply) reply = entry
  }
  return { heard, reply, memory }
}

function speakerLabel(speaker) {
  const s = String(speaker || '')
  if (s === 'user') return 'YOU'
  if (s.startsWith('user:')) return s.slice(5).toUpperCase()
  return 'TAU'
}

export default function TranscriptionOverlay({ state, visible = true, working = false, image = null }) {
  const history = state?.history || []
  const { heard, reply, memory } = useMemo(() => latestExchange(history), [history])

  const words = useMemo(() => (reply?.text ? String(reply.text).split(/\s+/).filter(Boolean) : []), [reply])
  const [sungCount, setSungCount] = useState(0)
  const timerRef = useRef(null)

  // Restart the karaoke sweep whenever a new reply lands. Keyed on the text itself (plus its
  // timestamp) so an identical reply to a repeated question still re-animates.
  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current)
    if (words.length === 0) {
      setSungCount(0)
      return undefined
    }
    if (prefersReducedMotion() || !visible) {
      setSungCount(words.length)
      return undefined
    }
    setSungCount(0)
    let index = 0
    const step = () => {
      index += 1
      setSungCount(index)
      if (index >= words.length) return
      timerRef.current = setTimeout(step, WORD_BASE_MS + words[index].length * WORD_PER_CHAR_MS)
    }
    timerRef.current = setTimeout(step, WORD_BASE_MS)
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reply?.text, reply?.timestamp, words.length])

  // While a turn is in flight, the newest exchange has a `heard` but no `reply` yet - that's when
  // the neuron field takes this space. It also holds the space open on a turn Tau is working
  // through before anything has been transcribed.
  const showNeurons = working && !reply
  const hasContent = Boolean(heard || reply) || showNeurons

  return (
    <div
      className={`response-surface ${visible && hasContent ? 'shown' : 'hidden'}`}
      aria-live="polite"
      aria-hidden={!(visible && hasContent)}
    >
      {heard && (
        <div className="response-heard">
          <span className="response-speaker">{speakerLabel(heard.speaker)}</span>
          <span className="response-heard-text">{heard.text}</span>
        </div>
      )}

      {memory && <div className="response-memory">{memory.text}</div>}

      {reply && (
        <div className="response-reply">
          {words.map((word, i) => (
            <span key={`${i}-${word}`} className={`karaoke-word ${i < sungCount ? 'sung' : ''}`}>
              {word}{' '}
            </span>
          ))}
        </div>
      )}

      {/* Phase 40 "visual answer card": a picture for a "what does X look like" reply (e.g. via
          research-mcp-server.search_images), Siri/Gemini-style. Local-turn state, not part of
          `state` - see App.jsx's lastImage for why - so it can outlive an old reply for a beat if
          a new command clears it before the next one lands; harmless, the image always tracks
          the most recent /api/chat response. */}
      {image?.url && (
        <a
          className="response-image"
          href={image.source_url || image.url}
          target="_blank"
          rel="noreferrer noopener"
        >
          <img src={image.url} alt={image.title || 'related image'} loading="lazy" />
          {(image.title || image.source) && (
            <span className="response-image-caption">
              {[image.title, image.source].filter(Boolean).join(' — ')}
            </span>
          )}
        </a>
      )}

      {showNeurons && <NeuronField active={working} />}

      {!reply && heard && !showNeurons && <div className="response-pending">…</div>}
    </div>
  )
}
