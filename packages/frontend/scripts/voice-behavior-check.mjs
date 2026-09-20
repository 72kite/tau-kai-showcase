/**
 * Behavioral check for the voice-invoke state machine (Phase 16: half-duplex gate, bounded
 * follow-up, wake-word-gated barge-in, explicit/stop-phrase closers, tightened wake match).
 *
 * useVoiceInvoke.js/useSpeech.js are browser-only (SpeechRecognition, Web Audio, MediaRecorder) -
 * nothing here runs in this repo's Python/pytest infrastructure, and there is no JS unit-test
 * framework in this package. This drives the REAL built app in a REAL Chromium (Playwright,
 * already a devDependency for scripts/visual-check.mjs) instead: a fake SpeechRecognition class is
 * injected before the app's own code runs, so `useVoiceInvoke`'s actual production logic - the
 * same code path a real browser's recognizer would drive - reacts to scripted transcripts exactly
 * as it would to a real one. Every assertion is on real, user-observable behavior (DOM state,
 * network calls the app itself made), not on internals.
 *
 * Always force-builds its own dist (VITE_VOICE_MODE=browser, separate --outDir) rather than
 * reusing whatever `dist/` a prior `npm run build` produced - a developer's local .env could be
 * building `local` mode (MediaRecorder, no SpeechRecognition), which would silently make every
 * scenario below a no-op instead of a real check.
 *
 *   node scripts/voice-behavior-check.mjs
 *
 * Exits non-zero (with a PROBLEMS list) if any scenario's assertions fail.
 */
import { chromium } from 'playwright'
import { createServer } from 'node:http'
import { readFile, mkdir, rm } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { extname, join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const ROOT = resolve(HERE, '..')
const DIST = resolve(ROOT, 'dist-voice-behavior-check')

const MIME = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.webmanifest': 'application/manifest+json',
}

function serveDist() {
  const server = createServer(async (req, res) => {
    const path = (req.url || '/').split('?')[0]
    let file = join(DIST, path === '/' ? 'index.html' : path)
    if (!existsSync(file)) file = join(DIST, 'index.html') // SPA fallback
    try {
      const body = await readFile(file)
      res.writeHead(200, { 'content-type': MIME[extname(file)] || 'application/octet-stream' })
      res.end(body)
    } catch {
      res.writeHead(404).end('not found')
    }
  })
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok(server)))
}

// A minimal, valid PCM WAV - silent, but real enough for AudioContext.decodeAudioData to accept
// and play for its real duration. That real duration is what makes the barge-in scenario able to
// tell "interrupted early" apart from "played out naturally."
function makeSilentWavBase64(durationSeconds, sampleRate = 8000) {
  const numSamples = Math.floor(durationSeconds * sampleRate)
  const dataSize = numSamples * 2 // 16-bit mono
  const buf = Buffer.alloc(44 + dataSize)
  buf.write('RIFF', 0)
  buf.writeUInt32LE(36 + dataSize, 4)
  buf.write('WAVE', 8)
  buf.write('fmt ', 12)
  buf.writeUInt32LE(16, 16)
  buf.writeUInt16LE(1, 20) // PCM
  buf.writeUInt16LE(1, 22) // mono
  buf.writeUInt32LE(sampleRate, 24)
  buf.writeUInt32LE(sampleRate * 2, 28)
  buf.writeUInt16LE(2, 32)
  buf.writeUInt16LE(16, 34)
  buf.write('data', 36)
  buf.writeUInt32LE(dataSize, 40)
  return buf.toString('base64') // data bytes already zeroed by Buffer.alloc = silence
}

// Injected before the app's own JS runs (page.addInitScript), so useVoiceInvoke's
// getSpeechRecognitionCtor() picks this up exactly as it would a real implementation.
// window.__voiceMocks.current always points at the live instance so the test can drive it, even
// across the hook's restart-on-end / effect-re-registration behavior.
function fakeSpeechRecognitionInitScript() {
  window.__voiceMocks = { current: null }
  class FakeSpeechRecognition {
    constructor() {
      this.continuous = false
      this.interimResults = false
      this.lang = 'en-US'
      this.onstart = null
      this.onresult = null
      this.onerror = null
      this.onend = null
      window.__voiceMocks.current = this
    }
    start() {
      Promise.resolve().then(() => this.onstart?.())
    }
    stop() {
      /* real API eventually fires onend; the hook nulls its handler before calling stop() during
         cleanup, so there is nothing useful to simulate here for these scenarios */
    }
    // Test-only driver: emits one final or interim result, shaped exactly like the real
    // SpeechRecognitionEvent - event.results[event.results.length-1][0].transcript + .isFinal.
    emit(transcript, isFinal) {
      const resultItem = [{ transcript }]
      resultItem.isFinal = isFinal
      this.onresult?.({ results: [resultItem] })
    }
  }
  window.SpeechRecognition = FakeSpeechRecognition
  window.webkitSpeechRecognition = FakeSpeechRecognition
}

const baseState = {
  transcription: { active: false, current_text: '', history: [] },
  security: { lockdown_active: false, intrusion_count: 0 },
  devices: { device_count: 1, online_count: 1 },
  vision: { camera_active: false, scene_description: '' },
  design: { active: false, title: '', description: '', svg: '', ascii_art: '' },
  recognition: { active: false, person_id: '', role: '', svg: '', ascii_art: '', recognized_at: '' },
}

async function setupPage(browser, { replyWavSeconds = 0.4 } = {}) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } })
  const page = await context.newPage()
  const chatCalls = []

  const json = (route, body) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })

  await page.route('**/api/**', (r) => json(r, {}))
  await page.route('**/api/health*', (r) =>
    json(r, { status: 'ok', version: '0.0.0', build: 'test', connected_servers: [] })
  )
  await page.route('**/api/state*', (r) => json(r, baseState))
  await page.route('**/api/approvals*', (r) => json(r, []))
  await page.route('**/api/people*', (r) => json(r, [{ person_id: 'zion', access_level: 'admin' }]))
  await page.route('**/api/devices*', (r) => json(r, []))
  await page.route('**/api/transcript/unified*', (r) => json(r, { history: [] }))
  await page.route('**/api/devices/register*', (r) => json(r, { ok: true }))
  await page.route('**/api/drafts*', (r) => json(r, []))
  await page.route('**/api/drafts/count*', (r) => json(r, { count: 0 }))
  await page.route('**/api/activity*', (r) => json(r, []))

  // Dynamic: records every turn the app actually sent, and always replies instantly so the
  // half-duplex/follow-up/barge-in timing is governed only by the WAV's real playback length.
  await page.route('**/api/chat*', async (r) => {
    const body = JSON.parse(r.request().postData() || '{}')
    chatCalls.push(body.text)
    await json(r, { reply: `ack: ${body.text}`, pending_approval_ids: [], recalled_memories: [] })
  })

  const wavB64 = makeSilentWavBase64(replyWavSeconds)
  await page.route('**/api/voice/speak*', (r) => json(r, { audio_base64: wavB64, format: 'wav' }))

  await page.addInitScript(fakeSpeechRecognitionInitScript)

  return {
    page,
    context,
    chatCalls,
    isListening: () => page.locator('.atom-container.listening').count(),
    isThinking: () => page.locator('.atom-container.thinking').count(),
    activityText: () => page.locator('.model-activity-text').first().textContent().catch(() => null),
    emit: (transcript, isFinal = true) =>
      page.evaluate(
        ([t, f]) => window.__voiceMocks.current?.emit(t, f),
        [transcript, isFinal]
      ),
    waitRecognition: () => page.waitForFunction(() => !!window.__voiceMocks?.current, null, { timeout: 5000 }),
  }
}

const scenarios = []
function scenario(name, description, fn) {
  scenarios.push({ name, description, fn })
}

scenario(
  'wake-command-reply-opens-followup',
  'Wake phrase + trailing command -> chat call -> reply spoken -> follow-up window opens after',
  async ({ page, chatCalls, emit, waitRecognition }) => {
    await waitRecognition()
    await emit('hey tau what is the weather', true)
    await waitFor(() => chatCalls.length >= 1, 5000, 'chat call never happened')
    if (chatCalls[0] !== 'what is the weather') {
      throw new Error(`expected trailing command 'what is the weather', got ${JSON.stringify(chatCalls[0])}`)
    }
    await waitForPageText(page, '.model-activity-text', /still listening \(\d+s\)/, 5000, 'follow-up countdown never appeared')
  }
)

scenario(
  'followup-accepts-bare-utterance',
  'A bare utterance (no wake phrase) heard during the follow-up window is treated as a new command',
  async ({ page, chatCalls, emit, waitRecognition }) => {
    await waitRecognition()
    await emit('hey tau what is the weather', true)
    await waitFor(() => chatCalls.length >= 1, 5000, 'first chat call never happened')
    await waitForPageText(page, '.model-activity-text', /still listening/, 5000, 'follow-up window never opened')
    await emit('what about tomorrow', true) // deliberately no wake phrase
    await waitFor(() => chatCalls.length >= 2, 5000, 'follow-up utterance never became a second chat call')
    if (chatCalls[1] !== 'what about tomorrow') {
      throw new Error(`expected follow-up command 'what about tomorrow', got ${JSON.stringify(chatCalls[1])}`)
    }
  }
)

scenario(
  'tightened-wake-match-rejects-embedded-phrase',
  '"hey taught me..." must NOT fire the wake phrase (old indexOf() bug: fired with "ght me..." as a bogus command)',
  async ({ page, chatCalls, emit, waitRecognition }) => {
    await waitRecognition()
    await emit('hey taught me something interesting today', true)
    await page.waitForTimeout(1500) // let any (wrongly) triggered effect run
    if (chatCalls.length > 0) {
      throw new Error(`wake phrase falsely matched an embedded occurrence - chat call: ${JSON.stringify(chatCalls[0])}`)
    }
    if (await page.locator('.atom-container.listening').count()) {
      throw new Error('atom entered listening state from an embedded false match')
    }
  }
)

scenario(
  'barge-in-interrupts-playback-early',
  'The wake phrase heard mid-reply stops playback and opens capture well before natural playback end',
  async ({ page, chatCalls, emit, waitRecognition }, { replyWavSeconds }) => {
    await waitRecognition()
    await emit('hey tau tell me a long story', true)
    await waitFor(() => chatCalls.length >= 1, 5000, 'first chat call never happened')
    await page.waitForTimeout(300) // let speak() flip the half-duplex gate on
    const bargeInAt = Date.now()
    await emit('hey tau stop that', true) // barge-in: wake phrase heard while "speaking"
    await waitFor(() => chatCalls.length >= 2, 3000, 'barge-in never interrupted playback / opened capture')
    const elapsed = Date.now() - bargeInAt
    const naturalPlaybackMs = replyWavSeconds * 1000
    if (elapsed >= naturalPlaybackMs) {
      throw new Error(
        `barge-in took ${elapsed}ms, not faster than the ${naturalPlaybackMs}ms reply - looks like it waited out playback instead of interrupting it`
      )
    }
    if (chatCalls[1] !== 'stop that') {
      throw new Error(`expected barge-in trailing command 'stop that', got ${JSON.stringify(chatCalls[1])}`)
    }
  }
)

scenario(
  'stop-phrase-sleeps-then-tap-wakes',
  '"stop listening" sleeps the wake phrase; an explicit atom tap still wakes it early',
  async ({ page, emit, waitRecognition }) => {
    await waitRecognition()
    await emit('tau, please stop listening now', true)
    await waitForPageText(page, '.model-activity-text', /resting.*tap to resume/i, 5000, '"resting" indicator never appeared')
    await page.locator('.atom-container').dblclick()
    await waitFor(async () => (await page.locator('.atom-container.listening').count()) > 0, 5000, 'atom tap did not wake it from sleep')
  }
)

scenario(
  'tap-to-cancel-during-capture',
  'Tapping the stage (not the atom) while capturing cancels it without submitting anything',
  async ({ page, chatCalls }) => {
    await page.locator('.atom-container').dblclick() // invoke, no trailing command
    await waitFor(async () => (await page.locator('.atom-container.listening').count()) > 0, 5000, 'invoke never opened capture')
    // Click the stage well away from the centered atom, so this doesn't land on the atom itself.
    await page.locator('.atom-stage').click({ position: { x: 5, y: 5 } })
    await waitFor(async () => (await page.locator('.atom-container.listening').count()) === 0, 5000, 'tap-to-cancel never closed capture')
    if (chatCalls.length > 0) {
      throw new Error(`cancel must not submit anything, but a chat call happened: ${JSON.stringify(chatCalls[0])}`)
    }
  }
)

async function waitFor(predicate, timeoutMs, message) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await predicate()) return
    await new Promise((r) => setTimeout(r, 100))
  }
  throw new Error(message)
}

async function waitForPageText(page, selector, pattern, timeoutMs, message) {
  await waitFor(async () => {
    const text = await page.locator(selector).first().textContent().catch(() => null)
    return text && pattern.test(text)
  }, timeoutMs, message)
}

async function main() {
  console.log('Building a dedicated browser-mode bundle (VITE_VOICE_MODE=browser)...')
  await rm(DIST, { recursive: true, force: true })
  // node_modules/.bin/vite (a shell shim on POSIX, a .cmd on Windows) needs `shell: true` to
  // resolve cross-platform; invoking vite's own JS entrypoint directly through the same `node`
  // running this script sidesteps that entirely.
  execFileSync(process.execPath, [join(ROOT, 'node_modules', 'vite', 'bin', 'vite.js'), 'build', '--outDir', DIST], {
    cwd: ROOT,
    env: { ...process.env, VITE_VOICE_MODE: 'browser' },
    stdio: 'inherit',
  })
  await mkdir(DIST, { recursive: true })

  const server = await serveDist()
  const { port } = server.address()
  const origin = `http://127.0.0.1:${port}`
  const browser = await chromium.launch()
  const problems = []

  for (const { name, description, fn } of scenarios) {
    const replyWavSeconds = name === 'barge-in-interrupts-playback-early' ? 3.0 : 0.4
    const ctx = await setupPage(browser, { replyWavSeconds })
    try {
      await ctx.page.goto(origin, { waitUntil: 'networkidle' })
      await fn(ctx, { replyWavSeconds })
      console.log(`ok    ${name} - ${description}`)
    } catch (e) {
      problems.push(`[${name}] ${e.message}`)
      console.error(`FAIL  ${name} - ${description}\n      ${e.message}`)
    } finally {
      await ctx.context.close()
    }
  }

  await browser.close()
  server.close()
  await rm(DIST, { recursive: true, force: true })

  console.log('')
  if (problems.length) {
    console.error(`${problems.length}/${scenarios.length} scenario(s) failed.`)
    process.exit(1)
  }
  console.log(`All ${scenarios.length} voice-behavior scenarios passed.`)
}

main()
