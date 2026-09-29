/**
 * Visual check harness for the Tau UI.
 *
 * The whole bridge API is HTTP polling (see src/hooks/useMCPResource.js), which means every state
 * this UI can be in is reachable by intercepting those routes - no Ollama, no Docker, no bridge,
 * no GPU. That's the point: this drives the real built app into each state and screenshots it, so
 * the visual work (6.C/6.F) can actually be looked at rather than only reasoned about.
 *
 * It is a *look-at-it* harness, not an assertion suite: it fails on console errors, request
 * failures, and horizontal overflow - the things that are unambiguously broken - and otherwise
 * leaves judgement to a human reading the PNGs. Taste isn't assertable.
 *
 * Accessibility (2026-09-12): every scene also runs axe-core (@axe-core/playwright, WCAG 2.1/2.2
 * A+AA rules) against the settled page. No accessibility audit had ever been run on this app
 * before - not "passed once and stayed clean," genuinely never checked - so `critical`/`serious`
 * violations fail the run the same way console errors do (automated tooling catches roughly half
 * of real accessibility issues by volume, per the tools' own documentation - this is a floor, not
 * a certification); `moderate`/`minor` findings print but don't fail, same "leave judgement to a
 * human" treatment as the screenshots themselves.
 *
 *   node scripts/visual-check.mjs [--theme dark] [--out DIR] [--viewport 1280x800]
 *
 * Serves the production build (npm run build first). Screenshots land in scripts/__screenshots__/.
 *
 * Found genuinely broken 2026-09-12 (nobody had run this since the kiosk admin panel was removed
 * - Phase 38/40): the `admin-drafts` scene clicked a toolbar ADMIN button that no longer exists
 * (admin moved to the standalone admin-frontend app), so every scene after it in iteration order
 * never ran. Removed rather than fixed - there's no kiosk admin surface left to screenshot.
 *
 * Also found the same day: the `thinking` scene's `.manual-invoke-input.press('Enter')` did not
 * reach React's onSubmit here, worked around at the time with `dispatchEvent('submit')` and left
 * as an open question - headless/CDP testing artifact, or a real risk for actual users?
 *
 * **Chased down for real, Phase 50 (2026-09-20), with a genuinely headed, focused browser
 * window** - it was real, not a testing artifact. A real Enter keypress (and a real click on the
 * SEND button - same `type="submit"` native path) raced the browser's own implicit-submit default
 * against React's synchronous state update (no `action` on the form let that default fire at all)
 * and lost often enough to matter: the browser logged "Form submission canceled because the form
 * is not connected" and `/api/chat` was never called. Fixed in `App.jsx`
 * (`submitManualCommand`/`handleManualKeyDown`/`handleManualSubmitClick`) by handling Enter and
 * the SEND click explicitly and calling `preventDefault()` before the browser ever queues its
 * native submit, instead of relying on `onSubmit` to win a race it demonstrably sometimes lost.
 * The form's `onSubmit` is now a no-op safety net, not the real path - so this scene uses a real
 * `.press('Enter')` below, the same as an actual user, not the old `dispatchEvent` workaround.
 */
import { chromium } from 'playwright'
import AxeBuilder from '@axe-core/playwright'
import { createServer } from 'node:http'
import { readFile, mkdir } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import { extname, join, resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const DIST = resolve(HERE, '..', 'dist')

// The footer compares the bundle's baked-in build stamp against /api/health. Read the same values
// the build used, so the mocked bridge agrees with the bundle under test and the footer isn't
// falsely flagged stale in every screenshot.
const pkgVersion = JSON.parse(
  await readFile(resolve(HERE, '..', 'package.json'), 'utf8')
).version
const buildStamp = process.env.TAU_BUILD_SHA || 'dev'

const args = process.argv.slice(2)
const argOf = (name, fallback) => {
  const i = args.indexOf(`--${name}`)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const THEME = argOf('theme', 'light')
const OUT_DIR = resolve(argOf('out', join(HERE, '__screenshots__')))
const [VW, VH] = argOf('viewport', '1280x800').split('x').map(Number)

const MIME = {
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
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

// ---------------------------------------------------------------------------
// Fixtures: the exact shapes the bridge really returns (see tau_core/web/server.py
// and ui-bridge-mcp-server's resources). Keep these honest - a fixture that drifts from the
// real payload makes the screenshot a lie.
// ---------------------------------------------------------------------------

const baseState = {
  transcription: { active: false, current_text: '', history: [] },
  security: { lockdown_active: false, intrusion_count: 0 },
  devices: { device_count: 12, online_count: 11 },
  vision: { camera_active: false, scene_description: '' },
  design: { active: false, title: '', description: '', svg: '', ascii_art: '' },
  recognition: { active: false, person_id: '', role: '', svg: '', ascii_art: '', recognized_at: '' },
}

// Inline rather than a checked-in fixture file: the visual-context scene needs *an* image to
// frame, and what it depicts is irrelevant - the thing under test is the frame, the crop marks,
// the caption and the data-link, not the picture. A data: URI also can't fail to load, so this
// scene can never go red because a fixture path moved. 4:3, mid-grey, so the grayscale filter and
// the 1px frame are both visible against it.
const PLACEHOLDER_IMAGE =
  'data:image/svg+xml;base64,' +
  Buffer.from(
    '<svg xmlns="http://www.w3.org/2000/svg" width="480" height="360">' +
      '<rect width="480" height="360" fill="#8a8a8a"/>' +
      '<rect x="40" y="40" width="400" height="280" fill="none" stroke="#3a3a3a" stroke-width="3"/>' +
      '<circle cx="240" cy="180" r="70" fill="none" stroke="#3a3a3a" stroke-width="3"/>' +
      '</svg>'
  ).toString('base64')

const exchange = [
  { speaker: 'user:zion', text: 'what did we decide about the kitchen lighting?', timestamp: '2026-07-14T10:00:01' },
  { speaker: 'memory', text: 'recalled: Kitchen lighting decision', timestamp: '2026-07-14T10:00:02' },
  {
    speaker: 'tau',
    text: 'You settled on warm 2700K strips under the cabinets, on a motion trigger after sunset. The overheads stay on the wall switch so a guest can always find the light.',
    timestamp: '2026-07-14T10:00:03',
  },
]

// Newest-first, as /api/activity really returns (see ActivityPanel).
const activity = [
  { timestamp: '2026-07-14T10:00:03', server: 'memory-mcp-server', tool: 'search_memory', outcome: 'executed', effect: 'allow' },
  { timestamp: '2026-07-14T10:00:02', server: 'utility-mcp-server', tool: 'get_time', outcome: 'executed', effect: 'allow' },
  { timestamp: '2026-07-14T10:00:01', server: 'proxmox-mcp-server', tool: 'apply_update', outcome: 'denied', effect: 'deny' },
  { timestamp: '2026-07-14T10:00:00', server: 'robotics-mcp-server', tool: 'patrol_route', outcome: 'pending', effect: 'require_approval' },
]

// Neurons only fire for events that arrive AFTER the hook's first (seeding) poll - that's the
// whole point of the seed, so a page opened mid-history doesn't flash stale calls. So the burst
// must contain genuinely NEW events on top of what was seeded, exactly like a real turn.
//
// Seeding just the `get_time` call leaves the other three to arrive during the turn, which is
// deliberate: they carry one executed, one denied, and one pending outcome, so the scene actually
// exercises all three neuron styles rather than only the happy path.
const activitySeed = [activity[1]]
const activityBurst = activity

const SCENES = {
  idle: {
    description: 'Atom centered, nothing in flight. The default the kiosk sits in.',
    state: baseState,
    activity: [],
  },
  'response-shown': {
    description: 'Reply landed: atom slid aside, karaoke line + recall note beside it.',
    state: { ...baseState, transcription: { active: false, current_text: '', history: exchange } },
    activity,
  },
  'visual-context': {
    description: 'Recall with a picture: framed visual-context block + data-link to the atom.',
    state: { ...baseState, transcription: { active: false, current_text: '', history: exchange } },
    activity,
    // Drives a real chat turn, because the picture is NOT part of the polled state - it comes
    // back on the /api/chat response and lives in App.jsx's `lastImage` (deliberately per-device;
    // see its comment). Setting `state` alone would show the text and never the image, which is
    // exactly the scene this is here to photograph.
    chatReply: {
      reply: 'The Lyra Vance piece is in the Neo-Atrium. Your visit is booked for next week.',
      image: {
        url: PLACEHOLDER_IMAGE,
        title: 'The Lyra Vance Installation, Neo-Atrium Rotunda',
        source: 'memory-mcp-server',
        source_url: 'https://example.invalid/lyra-vance',
      },
    },
  },
  thinking: {
    description: 'Turn in flight: atom aside, neuron field firing in the opened space.',
    state: {
      ...baseState,
      transcription: { active: true, current_text: '', history: exchange.slice(0, 1) },
    },
    activity: activitySeed,
    activityBurst,
  },
  'drawer-open': {
    description: 'SystemDrawer: six panels drawing in on a stagger.',
    state: baseState,
    activity,
    open: 'drawer',
  },
  'approval-pending': {
    description: 'Blocking approval overlay - the one place interrupting is correct.',
    state: baseState,
    activity,
    approvals: [
      {
        id: 'a1',
        server: 'proxmox-mcp-server',
        tool: 'apply_update',
        reason: 'Applies pending security updates to node pve-01 (requires human approval)',
        arguments: { node: 'pve-01', reboot_if_needed: true },
      },
    ],
  },
  'version-drift': {
    description: 'Stale cached PWA: bundle older than the bridge -> footer flags STALE.',
    state: baseState,
    activity: [],
    // A bridge on a different commit from this bundle - what a service-worker-cached kiosk looks
    // like after a redeploy. The build stamps must both be real (not 'dev') for the check to fire.
    health: { status: 'ok', version: '0.2.0', build: 'beef123', connected_servers: [] },
  },
  lockdown: {
    description: 'Lockdown active + camera live: toolbar alert state and the blue vision hue.',
    state: {
      ...baseState,
      security: { lockdown_active: true, intrusion_count: 2 },
      vision: { camera_active: true, scene_description: 'motion at the side gate' },
      transcription: { active: false, current_text: '', history: exchange },
    },
    activity,
  },
}

async function main() {
  if (!existsSync(DIST)) {
    console.error('dist/ not found - run `npm run build` first.')
    process.exit(1)
  }
  await mkdir(OUT_DIR, { recursive: true })

  const server = await serveDist()
  const { port } = server.address()
  const origin = `http://127.0.0.1:${port}`

  const browser = await chromium.launch()
  const problems = []

  // Phase 50: real screen-reader testing (a human running NVDA/JAWS/VoiceOver) hasn't happened on
  // this app and can't happen in this harness - nothing here replaces that. What this DOES check,
  // automatically, on the app's two real dialogs: the accessibility tree exposes the role/name a
  // screen reader would actually announce (not just DOM markup axe already checked), and Tab can't
  // escape the trap while the dialog is open. A floor under the real thing, not a substitute for it.
  const DIALOG_CHECKS = {
    'drawer-open': { selector: '.system-drawer', role: 'dialog', expectedName: 'SYSTEM STATUS' },
    'approval-pending': { selector: '.approval-queue-overlay', role: 'alertdialog', expectedName: 'APPROVAL REQUIRED' },
  }

  async function checkDialogA11yProxy(page, sceneName, { selector, role, expectedName, tabPresses = 12 }) {
    // getByRole matches on computed accessible role + name, the same lookup a screen reader's own
    // "find by role" would use - a better proxy than hand-walking a raw AX-tree snapshot (and the
    // only supported path: page.accessibility.snapshot() was removed in this Playwright version).
    const count = await page.getByRole(role, { name: expectedName }).count()
    if (count === 0) {
      problems.push(
        `[${sceneName}] a11y-proxy: no element with accessible role="${role}" and name "${expectedName}" ` +
        `was found - a screen reader would not announce this as a dialog.`
      )
    }

    for (let i = 0; i < tabPresses; i++) {
      await page.keyboard.press('Tab')
      const inside = await page.evaluate((sel) => {
        const container = document.querySelector(sel)
        return !!container && container.contains(document.activeElement)
      }, selector)
      if (!inside) {
        problems.push(
          `[${sceneName}] a11y-proxy: focus escaped the trapped dialog (${selector}) after ${i + 1} Tab press(es).`
        )
        break
      }
    }
  }

  for (const [name, scene] of Object.entries(SCENES)) {
    const context = await browser.newContext({
      viewport: { width: VW, height: VH },
      colorScheme: THEME,
      deviceScaleFactor: 2,
    })
    const page = await context.newPage()

    // Fail loudly on anything the app itself considers broken.
    page.on('console', (m) => {
      if (m.type() === 'error') problems.push(`[${name}] console: ${m.text()}`)
    })
    page.on('pageerror', (e) => problems.push(`[${name}] pageerror: ${e.message}`))
    page.on('requestfailed', (r) => {
      // Route-fulfilled requests never fail; anything here is a real miss.
      problems.push(`[${name}] request failed: ${r.url()} (${r.failure()?.errorText})`)
    })

    // Tool calls land *during* a turn, not before it. Flipping this after the command is
    // submitted is what makes neurons fire, and it has to be time/flag based rather than a
    // request counter: useModelActivity polls /api/activity from page load too, so a counter
    // would be spent before useNeuronActivity ever mounts (and the field would seed on the burst
    // and show nothing - which is exactly what happened the first time).
    let burstArmed = false
    const json = (route, body) =>
      route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })

    // NOTE: Playwright matches routes last-registered-first, so the catch-all must be registered
    // FIRST or it shadows every specific mock below it (which white-screens the app: components
    // that expect an array get `{}` and `.map` throws).
    await page.route('**/api/**', (r) => json(r, {}))
    // The bridge's identity. Must match the bundle's own build stamp, or StatusFooter correctly
    // reports drift and every screenshot grows a STALE flag. `scene.health` overrides it for the
    // one scene that deliberately exercises the drift path.
    await page.route('**/api/health*', (r) =>
      json(
        r,
        scene.health || {
          status: 'ok',
          version: pkgVersion,
          build: buildStamp,
          connected_servers: [],
          // Fixed, not Date.now()-derived: the toolbar's UPTIME readout has to appear in the
          // screenshots (it is absent, by design, until /api/health has answered once - see
          // useUptime.js), and a value that changed per run would make every PNG differ.
          uptime_seconds: 302400,
        }
      )
    )
    await page.route('**/api/state*', (r) => json(r, scene.state))
    await page.route('**/api/approvals*', (r) => json(r, scene.approvals || []))
    await page.route('**/api/people*', (r) => json(r, [{ person_id: 'zion', access_level: 'admin' }]))
    // Admin-dashboard reads. These had no stubs until 8.C added a scene that opens the dashboard -
    // no scene had ever photographed it - so the `**/api/**` catch-all handed useAdmin `{}` and
    // `devices.map` threw. Registered BEFORE the /register route below, since later routes take
    // precedence in Playwright and `**/api/devices*` would otherwise shadow it.
    await page.route('**/api/devices*', (r) =>
      json(r, [
        { device_id: 'kitchen-ipad-0001', name: 'Kitchen iPad', last_seen: '2026-07-15T10:02:11', blocked: false },
        { device_id: 'study-ipad-0002', name: 'Study iPad', last_seen: '2026-07-15T09:41:03', blocked: false },
      ])
    )
    await page.route('**/api/transcript/unified*', (r) => json(r, { history: exchange }))
    await page.route('**/api/devices/register*', (r) => json(r, { ok: true }))
    // Phase 16 admin portal: three more admin-gated reads the dashboard fetches on open. Same
    // failure mode as the ones above if left to the `**/api/**` catch-all - SecurityAdminPanel's
    // `.map` over `tier_policy` would throw on `{}`.
    await page.route('**/api/admin/system*', (r) =>
      json(r, {
        current_model: 'qwen2.5:7b-instruct',
        current_router_model: 'qwen2.5:7b-instruct',
        hardware: {
          recommended_model: 'qwen2.5:7b-instruct',
          gpu_vram_mb: 8192,
          gpu_count: 1,
          system_ram_mb: 32768,
          cpu_cores: 8,
        },
        uptime_seconds: 3600,
      })
    )
    await page.route('**/api/admin/people*', (r) =>
      json(r, { people: [{ person_id: 'zion', access_level: 'admin', face_count: 1, voice_count: 1 }] })
    )
    await page.route('**/api/admin/security*', (r) =>
      json(r, {
        require_voice_approval: false,
        voice_match_max_distance: 0.75,
        tier_policy: [
          {
            server: 'security-mcp-server', tool: 'exit_lockdown', min_tier: 'admin',
            reason: 'Clearing lockdown after a detected intrusion is admin-only.',
          },
        ],
      })
    )
    await page.route('**/api/admin/devices/*/block*', (r) => json(r, { device_id: 'x', blocked: true }))
    await page.route('**/api/admin/devices/*/unblock*', (r) => json(r, { device_id: 'x', blocked: false }))
    // Must be explicit: the `**/api/**` catch-all above returns `{}`, and useDrafts expects an
    // array - the same shape mismatch that white-screens the app for every other list endpoint.
    // Registered before /api/drafts* so the more specific count route wins (later routes take
    // precedence in Playwright, and `**/api/drafts*` matches /api/drafts/count too).
    await page.route('**/api/drafts*', (r) => json(r, scene.drafts || []))
    await page.route('**/api/drafts/count*', (r) => json(r, { count: (scene.drafts || []).length }))
    await page.route('**/api/activity*', (r) =>
      json(r, burstArmed && scene.activityBurst ? scene.activityBurst : scene.activity)
    )

    // Headless Chromium has no usable microphone, so the voice path can't be driven here. Take the
    // capture APIs away entirely, which puts the app on the text-input fallback it genuinely ships
    // for browsers/contexts without capture - the same path a plain-HTTP LAN kiosk gets, since
    // getUserMedia needs a secure context. A real supported path, not a test-only backdoor.
    //
    // Both engines have to be disabled: useVoiceInvoke picks by VITE_VOICE_MODE, and this repo's
    // .env builds `local` (MediaRecorder), not `browser` (SpeechRecognition).
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'mediaDevices', { value: undefined, configurable: true })
      Object.defineProperty(window, 'MediaRecorder', { value: undefined, configurable: true })
      Object.defineProperty(window, 'SpeechRecognition', { value: undefined, configurable: true })
      Object.defineProperty(window, 'webkitSpeechRecognition', {
        value: undefined,
        configurable: true,
      })
    })

    // `thinking` lives in useVoiceInvoke, not the API: it lasts exactly as long as /api/chat is in
    // flight. Hanging that request holds the state still to be photographed.
    if (name === 'thinking') {
      await page.route('**/api/chat*', () => {
        /* never fulfilled - the turn stays in flight */
      })
    }

    if (scene.chatReply) {
      await page.route('**/api/chat*', (r) => json(r, scene.chatReply))
    }

    await page.emulateMedia({ colorScheme: THEME })
    await page.goto(origin, { waitUntil: 'networkidle' })
    await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), THEME)

    // Phase 50 finding: BootScreen.jsx plays a fixed ~3.7s animation (GLYPH_MS+TAIL_MS+HOLD_MS+
    // FADE_MS) on every mount, independent of how fast the mocked /api/health answers - App.jsx
    // renders nothing else until it calls onDone(). Only scenes with a Playwright action that
    // happens to auto-wait for an element (drawer-open's click, thinking's dblclick+
    // waitForSelector) were incidentally waiting long enough for it to finish; every other scene's
    // screenshot AND axe-core pass was capturing the boot screen instead of the real app the whole
    // time - a genuine a11y gap in the harness itself, not just a cosmetic one, since it means most
    // of this file's "zero critical/serious violations" claims were never actually checking the
    // screens they were named for. Wait for it to unmount before any scene proceeds.
    await page.locator('.boot-screen').waitFor({ state: 'detached', timeout: 10000 })

    if (scene.open === 'drawer') {
      await page.getByRole('button', { name: /DEVICES/i }).first().click()
    }

    if (name === 'visual-context') {
      // Same path the thinking scene uses to reach the input, but the turn actually completes -
      // so App.jsx receives the mocked reply's `image` and the framed block mounts.
      await page.locator('.atom-container').dblclick()
      await page.locator('.manual-invoke-input').fill('what did we decide about the Lyra Vance piece?')
      await page.locator('.manual-invoke-input').press('Enter')
      await page.waitForSelector('.visual-context-frame img', { timeout: 5000 })
      // The data-link samples the atom's orbiting node on an interval and draws nothing until
      // both ends resolve (DataLink.jsx) - wait for a strand rather than a fixed sleep, so this
      // scene fails loudly if the link ever stops attaching instead of quietly photographing a
      // missing one.
      await page.waitForSelector('.data-link path', { timeout: 5000 })
    }

    if (name === 'thinking') {
      await page.locator('.atom-container').dblclick() // invoke, exactly as a user does
      await page.locator('.manual-invoke-input').fill('what did we decide about the kitchen lighting?')
      // A real .press('Enter') now works (Phase 50 fix, see the file docstring above) - Enter is
      // handled explicitly in App.jsx before the browser's native implicit-submit default can ever
      // fire, so this is the same path an actual user's keypress takes, not a workaround.
      await page.locator('.manual-invoke-input').press('Enter')
      await page.waitForSelector('.neuron-field.active', { timeout: 5000 })
      await page.waitForTimeout(1200) // let the field take its seeding poll first
      burstArmed = true // ...then Tau starts calling tools
      await page.waitForSelector('.neuron-node', { timeout: 5000 })
      await page.waitForTimeout(1500) // let the dendrites finish drawing
    }

    await page.waitForTimeout(1200) // let draw-in wipes and the karaoke sweep settle

    // Hard failure: the page must never scroll sideways on a kiosk.
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth
    )
    if (overflow > 0) problems.push(`[${name}] horizontal overflow: ${overflow}px`)

    // Accessibility: WCAG 2.1/2.2 A+AA. Scoped to what's actually visible - a hidden overlay
    // failing a rule nobody can currently see would be real noise, not a real finding, on a page
    // that intentionally keeps several overlays mounted-but-hidden at once (ApprovalQueue,
    // TranscriptionOverlay, SystemDrawer all render even when closed, gated by CSS/aria-hidden).
    const axeResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag22aa'])
      .analyze()
    for (const violation of axeResults.violations) {
      const line = `[${name}] a11y ${violation.impact}: ${violation.id} - ${violation.help} (${violation.nodes.length} node(s))`
      if (violation.impact === 'critical' || violation.impact === 'serious') {
        problems.push(line)
        for (const n of violation.nodes) {
          problems.push(`    ${JSON.stringify(n.target)} :: ${n.failureSummary?.replace(/\n/g, ' ')}`)
        }
      } else {
        console.log('  ' + line)
      }
    }

    if (DIALOG_CHECKS[name]) {
      await checkDialogA11yProxy(page, name, DIALOG_CHECKS[name])
    }

    const file = join(OUT_DIR, `${name}.${THEME}.png`)
    await page.screenshot({ path: file })
    console.log(`${name.padEnd(18)} ${scene.description}`)
    await context.close()
  }

  await browser.close()
  server.close()

  if (problems.length) {
    console.error('\nPROBLEMS:')
    for (const p of problems) console.error('  ' + p)
    process.exit(1)
  }
  console.log(`\nOK - ${Object.keys(SCENES).length} scenes -> ${OUT_DIR}`)
}

main()
