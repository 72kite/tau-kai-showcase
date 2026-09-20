// Pure string-matching logic for the voice-invoke state machine (useVoiceInvoke.js) - split out
// so it's testable without a browser, React, or SpeechRecognition (see
// scripts/test-voice-match.mjs, a plain-Node script with zero test-framework dependency, matching
// this repo's existing framework-free script convention).

// Explicit closers (Phase 16 next slice): saying one of these puts the wake phrase to sleep for a
// while - a deliberate "stop listening", not just letting the current capture time out. Substring
// match (like the wake phrase itself), so "okay, stop listening" still hits.
export const SLEEP_PHRASES = ['stop listening', 'go away']

export function containsSleepPhrase(text) {
  const lower = text.toLowerCase()
  return SLEEP_PHRASES.some((p) => lower.includes(p))
}

// Loose-match tightening: the wake phrase must sit at a word boundary on both sides, not just
// appear anywhere as a substring - "hey taught me" no longer fires "hey tau" with "ght me" as a
// bogus trailing command. Returns the phrase's start index, or -1 if no boundary-respecting
// occurrence exists (skipping past embedded false matches to check for a later real one).
export function findWakePhrase(transcript, phrase) {
  const lower = transcript.toLowerCase()
  const isWordChar = (c) => /[a-z0-9]/i.test(c)
  let from = 0
  while (from <= lower.length) {
    const idx = lower.indexOf(phrase, from)
    if (idx === -1) return -1
    const before = idx === 0 ? '' : lower[idx - 1]
    const afterIdx = idx + phrase.length
    const after = afterIdx >= lower.length ? '' : lower[afterIdx]
    if (!isWordChar(before) && !isWordChar(after)) return idx
    from = idx + 1
  }
  return -1
}
