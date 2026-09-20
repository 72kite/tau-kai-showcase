/**
 * Unit tests for the wake-phrase / sleep-phrase matching logic (src/utils/voiceMatch.js),
 * extracted from useVoiceInvoke.js specifically so it's testable without a browser, React, or
 * SpeechRecognition. Plain Node, zero test-framework dependency - matching this repo's existing
 * framework-free script convention (see scripts/visual-check.mjs). Exits non-zero on any failure.
 *
 *   node scripts/test-voice-match.mjs
 */
import { containsSleepPhrase, findWakePhrase } from '../src/utils/voiceMatch.js'

const WAKE = 'hey tau'
let failures = 0

function check(description, actual, expected) {
  const pass = actual === expected
  if (!pass) {
    failures++
    console.error(`FAIL  ${description}\n      expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
  } else {
    console.log(`ok    ${description}`)
  }
}

// --- findWakePhrase: the basics ---------------------------------------------------------------
check('matches the phrase alone', findWakePhrase('hey tau', WAKE), 0)
check('matches at the start with trailing command', findWakePhrase('hey tau what time is it', WAKE), 0)
check('matches mid-sentence', findWakePhrase('ok hey tau please stop', WAKE), 3)
check('matches case-insensitively', findWakePhrase('HEY TAU, hello', WAKE), 0)
check('no match when phrase absent', findWakePhrase('good morning', WAKE), -1)

// --- findWakePhrase: the tightened boundary behavior (Phase 16) -------------------------------
// The bug this fixes: "hey taught me" contains "hey tau" as a bare substring, with "ght me" left
// over as a bogus trailing command - a real false trigger under the old indexOf() check.
check('rejects the phrase embedded in a longer word (no boundary after)', findWakePhrase('hey taught me something', WAKE), -1)
check('rejects the phrase embedded in a longer word (no boundary before)', findWakePhrase('ahoyhey tau', WAKE), -1)
check(
  'finds a later, boundary-respecting match after skipping an embedded false one',
  findWakePhrase('heytaught but then hey tau for real', WAKE),
  19
)
check('a lone false-embedded occurrence with nothing legitimate after it still misses', findWakePhrase('heytaught me', WAKE), -1)
check('punctuation counts as a boundary', findWakePhrase("well, hey tau!", WAKE), 6)

// --- containsSleepPhrase --------------------------------------------------------------------
check('detects "stop listening" alone', containsSleepPhrase('stop listening'), true)
check('detects "stop listening" inside a longer sentence', containsSleepPhrase('okay, stop listening now'), true)
check('detects "go away"', containsSleepPhrase('go away please'), true)
check('is case-insensitive', containsSleepPhrase('STOP LISTENING'), true)
check('does not fire on unrelated speech', containsSleepPhrase('what is the weather today'), false)
check('does not fire on a partial phrase', containsSleepPhrase('please stop'), false)

console.log('')
if (failures > 0) {
  console.error(`${failures} test(s) failed.`)
  process.exit(1)
}
console.log('All voice-match tests passed.')
