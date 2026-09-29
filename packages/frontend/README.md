# Tau UI — Frontend

The web frontend for Project Tau — see `project-tau-plan.md` Section 6 (Phase 3). Primary deployment target: a browser on an **older iPad**, locked to this page in kiosk mode.

## Visual design

**E-ink aesthetic**: stark black (#000000) and white (#ffffff), no gradients, shadows, or glows. Monospaced typography (Roboto Mono, JetBrains Mono). SVG stroke-draw animations for both the atom and any engineering drawing Tau produces.

**Layout — one toolbar, one stage (Phase 6.F).** Earlier drafts swapped the atom out for a full-screen panel whenever voice/vision/devices were active; that's long gone. The layout since 6.F is two rows:

1. **Top toolbar** (`TopBar.jsx`): all the chrome, in one bounded bar — lockdown/devices/vision/approvals/people readouts on the left, then attach / mic / admin / theme toggle / clock on the right. This consolidates what used to be a thin top strip (clock + theme, 6.C) *and* a separate bottom status bar. Tapping a readout opens `SystemDrawer`, a bottom sheet with the detail — the only genuinely modal UI in the app, alongside the approval-queue overlay (approvals are the one case where interrupting the user is correct).
2. **Stage**: the atom, centered and dominant. It gets the entire stage unless there's an active design to draw, in which case `DesignDrawing` takes the other half.

**The atom moves; it never unmounts.** When Tau replies, the atom slides aside *within its own box* and the response surface (`TranscriptionOverlay.jsx`) takes the space next to it. The atom is never replaced, never paused, and never shrunk to a corner — the grid track around it animates, and the atom re-centers when the response goes away.

**Only the latest exchange is on screen, and only for 30s.** The response surface shows what Tau heard, what recall surfaced, and what Tau said — then auto-hides. There is no scrollable log on the kiosk: per Phase 6.D the durable cross-device record lives admin-side in the unified log, not on a screen anyone can walk up to and scroll back through. A faint `↑ LAST REPLY` affordance brings the last exchange back (tapping the atom's box does the same).

**Karaoke transcript.** Tau's reply renders as one flowing line whose words come up from low contrast to full ink as a sweep passes. This is a *reading-cadence approximation, not real TTS sync* — the bridge publishes no word timings from Piper, so there's nothing to sync to, and on a long reply the sweep will drift against the audio. Real timings would need an alignment channel through `voice-mcp-server`. `prefers-reduced-motion` reveals the whole line at once.

**Spoken replies (Phase 13).** After a **voice-initiated** turn, Tau reads its (short) answer aloud: `useSpeech.js` POSTs the reply to `POST /api/voice/speak` (→ `voice-mcp-server.speak` → Piper) and plays the returned WAV via Web Audio. Typed turns stay silent — a `source` tag on `useVoiceInvoke` distinguishes voice from the manual/attach path. A per-device **mute toggle** (♪ glyph in the toolbar, struck-through when muted) is persisted in localStorage. It's **best-effort**: if Piper isn't deployed the endpoint returns 503 and the UI just stays silent — the text reply is on screen regardless. This is separate from the karaoke sweep above (which is still a reading-cadence approximation, not synced to this audio). Needs a secure context for autoplay, same as mic capture.

**Neuron field.** While Tau is working, that same space — opened by the atom sliding aside, and otherwise empty until the reply lands — fills with a live picture of the turn: one neuron per MCP server being called, firing down its dendrite on each call. It's the audit trail as a picture, not decoration: every neuron is backed by a real CDG-audited tool call from `/api/activity`, and denied/pending calls are drawn distinctly (hollow node, dashed dendrite) because those are the ones worth noticing. It gives way to the reply the moment one arrives.

**Everything draws itself in.** `DrawIn` wraps an element so it's revealed by a wipe led by a thin ink pen-line, instead of just appearing — panels, the toolbar, approval cards. Siblings stagger, so a drawer reads as a hand working down the page. It's a wipe rather than the `stroke-dashoffset` trace `DesignDrawing` uses on Tau's SVG, because a wipe reveals *any* content (text, borders, canvases) with one primitive and can't fight a component's existing border the way an SVG outline traced over it would.

## Architecture

Built with **Vite + React + Three.js**, talking to `tau-core`'s HTTP bridge (`tau_core.web.server`, Python/FastAPI) rather than MCP directly — the frontend never talks to MCP servers itself.

```
frontend (browser)  --HTTP polling-->  tau-core web bridge  --MCP-->  ui-bridge-mcp-server
                                              |
                                        TauCoreHost.call_tool (CDG applies identically
                                        to browser-initiated actions as to the LLM path)
```

- `src/App.jsx` — root layout: toolbar + stage, approval overlay, system drawer, and the `responseShown`/auto-hide timer driving the atom's slide-aside and the response's 30s fade.
- `src/components/TopBar.jsx` — the single consolidated toolbar (6.F): status readouts, attach/mic/admin/theme controls, and the clock. The clock ticks from the browser's own wall time, not a network poll (`utility-mcp-server.get_time` exists so the *model* knows the time, not the UI).
- `src/components/StatusFooter.jsx` + `src/hooks/useVersion.js` — the far-bottom identity strip (Phase 7.A): build version left, device name/short-id right. The version is baked in at build time by Vite's `define` (see `vite.config.js`, fed by `TAU_BUILD_SHA`); the hook compares it against `GET /api/health` and flags **STALE** when this screen is serving an older bundle than the bridge is running — the failure a service-worker-cached kiosk otherwise has no symptom for. It only fires on a positive mismatch of two known values, never on absence or a placeholder.
- `src/components/Atom.jsx` — Three.js 3D atom. Detects likely-older-iPad hardware (`src/utils/device.js`) and scales back geometry detail / caps frame rate accordingly. Reactive outer ring: breathes with the speaker's live mic amplitude while listening (`useMicLevel.js` - local Web Audio analysis, no audio leaves the device) and tumbles gyroscope-style around the nucleus while thinking, easing flat when the reply lands. Invoke is **double-tap** (single taps are deliberately inert so brushing a mounted kiosk doesn't start a session); keyboard Enter/Space invokes on a single press.
- `src/components/DesignDrawing.jsx` — renders the engineering sketch Tau produces (`ui://design` resource). Prefers an SVG fragment the LLM generated (see the drawing-accuracy contract in `tau-core/src/tau_core/llm/agent.py`'s system prompt); falls back to an ASCII-art rendering. Animates each shape's stroke-draw using `getTotalLength()` per element, staggered — works on arbitrary generated SVG, not just pre-authored paths. Mounted only while there *is* an active design (6.F), so the atom owns the whole stage the rest of the time.
- `src/components/TranscriptionOverlay.jsx` — the response surface: the latest exchange (heard / recalled / replied) as a karaoke line, shown beside the atom. `latestExchange()` walks history backwards for the current turn and deliberately stops at the newest user utterance, so a previous turn's reply can never leak into the current one. Hands its space to `NeuronField` while a turn is still in flight.
- `src/components/NeuronField.jsx` + `src/hooks/useNeuronActivity.js` — the neuron-style active state. The hook's first poll **seeds without firing**, so opening the page mid-history doesn't flash a burst of neurons for calls that happened minutes ago; freshness is event-*change* driven rather than timestamp-parsed, so it never depends on the audit clock's timezone matching the browser's.
- `src/components/DrawIn.jsx` — the generative "drawn" reveal primitive (`delay` to stagger siblings, `direction` for the pen's travel). Fully honors `prefers-reduced-motion`.
- `src/components/SystemDrawer.jsx` — bottom-sheet detail view wrapping `DevicePanel`, `SecurityPanel`, `VisionPanel`, `MemoryPanel`, `ActivityPanel`.
- `src/components/MemoryPanel.jsx` — browse/search Tau's Memory Tree (the same store the assistant recalls from every turn) and reinforce a memory's score (▲). Reads are CDG-allowed; creating/editing memories deliberately stays behind the human-approval queue.
- `src/components/ActivityPanel.jsx` — live audit feed (`GET /api/activity`): every tool call with its CDG effect and outcome, including denied/queued/clarified calls. The project's auditability pillar, visible on the kiosk.
- `src/components/ApprovalQueue.jsx` — pending CDG approvals, wired to `useApprovals` for real approve/deny actions.
- `src/hooks/useMCPResource.js` — polls one MCP Resource through the bridge (`GET /api/resources/{server}/{uri}`). Deliberately polling, not WebSocket: older iPads on home Wi-Fi drop long-lived sockets more often than a single short GET, and polling self-heals on the next tick without reconnect logic.
- `src/hooks/useApprovals.js` — polls `/api/approvals`, exposes `approve(id)`/`deny(id)`.
- `src/hooks/useChat.js` — POSTs one utterance to `/api/chat`, returns the reply.
- `src/hooks/useVoiceInvoke.js` — wake-word listening + atom-click invoke state machine; see "Voice invoke" below.
- `src/hooks/useKioskMode.js` — requests fullscreen on first touch, suppresses pinch/double-tap-zoom gestures (not the browser's zoom accessibility feature — that stays available via the viewport meta tag).

## Setup & development

```bash
cd frontend
npm install
npm run dev       # Vite dev server at http://localhost:3000
```

You also need `tau-core`'s web bridge running (separate process, separate port):

```bash
cd ../tau-core
python -m tau_core.web   # binds 0.0.0.0:8000 by default
```

Set `VITE_TAU_API_BASE` in `.env` (see `.env.example`) if the bridge isn't reachable at `http://<hostname>:8000`.

For production:
```bash
npm run build
npm run preview
```

## Tablet / kiosk deployment

Point an iPad's browser (locked via MDM or Guided Access) at wherever the built frontend is served, with `tau-core`'s web bridge reachable on the same network. `viewport-fit=cover` and the `apple-mobile-web-app-*` meta tags in `index.html` support both native fullscreen (iOS 16+) and "Add to Home Screen" standalone mode (older iOS, which lacks the element Fullscreen API).

## Project Archer (Phase 5 mobile companion)

The build plan describes Project Archer as "a thinned-down MCP Host on Android/iOS." What's
actually built here is a pragmatic MVP of that: this same frontend, made installable as a PWA
(`public/manifest.json`, `public/sw.js`, registered in `main.jsx`), reaching `tau-core`'s web
bridge over Tailscale/WireGuard instead of the home LAN by pointing `VITE_TAU_API_BASE` at the
Tailscale address. It is **not** a separate native app and does not itself hold an MCP Host or
run any model locally — it is a thin client of the same bridge the kiosk tablet uses, exactly
like the tablet is. That's a deliberate scope call, not an oversight: a genuinely offline-capable
"thinned-down MCP Host" (running a small local model, caching state locally, reasoning without a
network round-trip) is a materially larger, platform-specific undertaking (native Swift/Kotlin or
React Native, on-device model runtime) that wasn't attempted here.

The service worker (`public/sw.js`) only caches the app shell (HTML/JS/CSS/icons) via
stale-while-revalidate, so the installed app opens instantly even over a bad Tailscale
connection - it never caches `/api/` responses or anything cross-origin. Live state (transcript,
telemetry, approvals, lockdown status) always goes straight to the network; caching any of that
would mean the app could show stale security state, which is worse than showing nothing.

**Setup**: install the frontend on the phone via the browser's "Add to Home Screen" /
"Install App" prompt, pointed at a URL where `tau-core`'s web bridge is reachable over your VPN
(set `VITE_TAU_API_BASE` accordingly before building, or serve the built app from somewhere that
proxies `/api/` to the bridge).

## Recall transparency & file attachments (2026-07-11)

**Recall lines in the transcript**: when the assistant's Memory Tree recall surfaces context
for a turn, the bridge mirrors a `speaker="memory"` line ("recalled: Kitchen lighting
decision") into the shared transcript, rendered as a quiet dotted-border note - every client
can see *why* Tau knew something, not just what it said.

**File attachments**: drag a file anywhere onto the app (a full-viewport DROP FILE target
appears), or tap ATTACH in the status bar for a picker. Files are base64'd and sent with the
next chat turn (`POST /api/chat` `attachments`); all extraction happens server-side in the
bridge so every client behaves identically - text files are decoded, PDFs go through pypdf,
images are described by `vision-mcp-server.describe_scene` through the audited `call_tool`
path. 10MB/file, 5 files/turn. Honest constraint: the local model is text-only, so images
reach it as vision-mcp-server's description (currently the brightness/edges/colors stub),
not as pixels - a vision-capable Ollama model (e.g. qwen2-vl) is the upgrade path.

## Multi-user voice identity (2026-07-12)

Local voice mode identifies **who** is speaking from the same clip it transcribes: identified
turns show the person's name on their chat bubble (`ZION` instead of `YOU`) on every client.
Unknown voices stay `YOU` - identification fails toward "unknown", never toward a guess.

**Enrollment** (PeoplePanel in the SystemDrawer): type a name, record a 5s sample, then approve
*two* cards in the approval queue (voiceprint computation and biometric storage are separately
CDG-gated on purpose), tapping CONTINUE after each. **Verified approvals**: with
`TAU_REQUIRE_VOICE_APPROVAL=true` on the bridge, approving/denying anything requires reading a
random 4-word challenge phrase aloud - the words must match (Whisper) and the voice must match
an enrolled person (voiceprint), which defeats replayed recordings. The flow appears inline in
the approval overlay only when the bridge demands it (HTTP 428).

Deployment note: the voiceprint model (speechbrain) must be installed where voice-mcp-server
runs; until then enrollment/identification degrade cleanly to "unavailable"/"unknown".

## Design principles

1. **High contrast**: no opacity for information-bearing state, no soft edges. Borders are 1–4px solid black. The one intentional exception is `SystemDrawer`'s semi-transparent scrim, since it's a genuine modal.
2. **Nothing state-switches to show information.** If a new fact needs to be visible, it appears in its permanent region; it doesn't cause another region to disappear. The transcript's collapse/rise (below) is the one deliberate exception: it's a height/opacity transition on an always-mounted element (history and scroll position survive the transition either way), not a mount/unmount swap - the atom is never interrupted by it.
3. **Touch-first**: no hover-dependent interactions, 44px+ minimum touch targets throughout.
4. **Older-hardware aware**: 3D geometry detail and frame rate scale down automatically on detected low-power tablets rather than assuming desktop-class GPU/CPU.

## Voice invoke: wake word + atom double-tap

Both voice modes capture a spoken command and POST it to `tau-core`'s `/api/chat` endpoint
(`tau_core/web/server.py`), a thin wrapper around `TauAssistant.chat()`. Every tool call the model
decides to make from that turn still flows through `TauCoreHost.call_tool` and the CDG exactly as
it would from any other caller - this isn't a new privileged path, just a new way to start a turn.
The transcript rises (see above) for the duration of the turn and for `AUTO_HIDE_MS` (10s)
afterward, then auto-collapses. `Atom.jsx`'s pulse animation distinguishes `isListening`
(capturing the command) from `isThinking` (waiting on the reply).

**Local mode (`VITE_VOICE_MODE=local`, the default)** — fully offline, no audio ever leaves the
LAN. Hands-free wake word comes from `voice-mcp-server`'s `detect_wake_word` (openWakeWord,
on-device ONNX): `useWakeWord.js` polls rolling ~1.3s windows against `/api/voice/wake`, and a
detection calls the same `invoke()` the atom double-tap uses. Because detection is acoustic-only
(no transcript), it's a **two-beat gesture** - say the wake word, pause, then the command - unlike
browser mode's one fluid sentence. Once invoked, capture records via `MediaRecorder` with
RMS-based voice-activity detection (stops ~3s after you finish speaking - fillers and thinking
pauses survive; gives up after 8s of nothing; 25s hard cap), then POSTs the clip to `tau-core`'s
`/api/voice/transcribe`, which calls `voice-mcp-server.transcribe` (CDG-audited) against the local
faster-whisper service (`infra/whisper`). The toolbar's mic icon goes green while capturing, and
the exact capture state (`LOCAL-READY` / `RECORDING` / `TRANSCRIBING`) survives in its tooltip.
**The shipped wake-word model is a locally-trained "hey tau"** (`voice-mcp-server`'s
`models/hey_tau.onnx`, used by default - see that package's README for the measured recall/
false-accepts curve; recall is modest, so double-tap remains the reliable fallback).
`getUserMedia`/`MediaRecorder` support is close to universal (notably works on iOS Safari, unlike
`SpeechRecognition` below), so this is also the more broadly-compatible mode for the primary
kiosk-tablet target.

**Bounded conversational follow-up (Phase 17).** The moment Tau finishes *speaking* a voice reply,
a short window opens (`VITE_FOLLOWUP_WINDOW_MS`, default 7s) in which a **bare** utterance — no wake
phrase — continues the conversation; silence closes it back to idle. It arms off a real
"playback ended" signal from `useSpeech` (not the `speaking` flag, which also drops on a muted or
failed reply where nothing was spoken), so a phantom window never opens. `ModelActivity` shows a
"still listening (Ns)" countdown driven off the hook's own timer, so it can't drift from the real
window. Browser mode relaxes the wake requirement in place; local mode reuses the capture engine
with a shorter no-speech leash. Set `VITE_FOLLOWUP_ENABLED=false` to require the wake phrase on
every turn.

**Wake-word barge-in (Phase 19).** Say the wake phrase *while* Tau is speaking to interrupt the
reply and start a new command, instead of waiting for it to finish. In browser mode the recognizer
watches for the wake phrase even under the half-duplex gate — only the **full** wake phrase
interrupts (ordinary words, and Tau's own voice, stay dropped, using the same boundary-aware
matcher as passive wake detection - see `utils/voiceMatch.js`), and on a match `useSpeech.stop()`
halts playback before capture opens. In local mode the wake loop, normally suspended during
playback, stays live when barge-in is on (safe: AEC cancels Tau's own voice). It's a per-device
toggle (the ✋ glyph in the toolbar, struck through when off), persisted in localStorage; default
from `VITE_BARGE_IN_DEFAULT`. An atom tap always barges in too, either mode - `invoke()` itself
stops playback first if Tau is mid-reply, so a manual tap is never silently dropped under it.
Interrupting a reply that's still being *generated* (the `thinking` state, before any audio) is
out of scope here — barge-in targets the spoken reply.

**Explicit closers.** Two ways to deliberately end a listening session rather than let it time
out: tapping anywhere on the stage while a capture is active (invoked or follow-up) cancels it
without submitting whatever was heard so far; saying a sleep phrase ("stop listening", "go away" -
`utils/voiceMatch.js`) puts the wake word to sleep for 45s regardless of mode (idle, invoked, or
follow-up), then it wakes itself back up. An atom tap always overrides sleep early - the phrase
only suppresses the *passive* wake word, never a deliberate tap.

**Browser mode (`VITE_VOICE_MODE=browser`)** — listens continuously via `SpeechRecognition` for a
configurable wake phrase (`VITE_WAKE_PHRASE`, default "hey tau"), matched as a case-insensitive
substring of the live transcript; wake word and command can arrive in one fluid sentence since
the recognizer already has the text. When `SpeechRecognition` isn't supported (notably weak/absent
on older iOS Safari) or the browser denies microphone access, tapping the atom instead reveals an
inline text-input fallback so the atom-click path still works without voice at all.

**Real constraints, not glossed over:**
- Both modes require a secure context (HTTPS or `localhost`) for microphone access in most
  browsers. A kiosk tablet served over plain HTTP on the LAN gets **no** wake-word/voice capture
  at all - only the text-input fallback.
- Browser mode's `SpeechRecognition` streams audio to Google's cloud servers for recognition -
  the one piece of this project that isn't local-only. Local mode exists specifically to avoid
  that; it's the default for that reason.
- Browser mode has no echo cancellation on Chrome's own mic (see `utils/audioConstraints.js`), so
  it's gated purely in software (`useVoiceInvoke.js`'s half-duplex gate + a short post-playback
  cooldown) against picking up Tau's own TTS output as a new command - a timing mitigation, not a
  root-cause fix. Local mode's `MediaRecorder` capture gets real `echoCancellation: true`
  instead, which is the actual fix.

## Visual check

```bash
npm run visual-check          # light theme
npm run visual-check:dark     # dark theme
```

Drives the real production build in headless Chromium through seven states and screenshots each into `scripts/__screenshots__/` (gitignored — regenerate rather than commit). **No bridge, no Ollama, no Docker, no GPU:** the entire bridge API is HTTP polling, so `scripts/visual-check.mjs` intercepts those routes and serves fixtures. The states are idle, response-shown, thinking (neuron field), drawer-open, approval-pending, version-drift, and lockdown.

It is a **look-at-it harness, not an assertion suite**. It fails on console errors, page errors, failed requests, and horizontal overflow — things that are unambiguously broken — and leaves the rest to a human reading the PNGs, because taste isn't assertable. It exists because this UI's whole point is how it looks, and "the bundle built" says nothing about that. It immediately paid for itself: see the plan's 6.F/6.C notes for the four real defects the first run caught, none of which a build check could have.

**Accessibility (2026-09-12):** every scene also runs [`@axe-core/playwright`](https://github.com/dequelabs/axe-core-npm) against the settled page (WCAG 2.1/2.2 A+AA rule tags). `critical`/`serious` violations fail the run the same as a console error; `moderate`/`minor` findings print but don't fail. No audit had ever been run before this — it immediately caught three real under-contrast failures (`.bar-value.quiet`, `.response-speaker`, `.footer-build`), all from the same root cause: CSS `opacity` used to de-emphasize already-muted text, which *compounds* with whatever color sits underneath rather than landing at a predictable contrast ratio. Fixed by replacing opacity-based dimming with a real calibrated color (`--faint` in `index.css`) at each of those three call sites. Also bumped every toolbar button (`.bar-item`, `.bar-icon`) to a 44×44 CSS px minimum tap target — WCAG 2.2 AAA (2.5.5), not the AA floor (24px, 2.5.8) axe's automated `target-size` rule checks and this app already cleared — because this is a wall-mounted kiosk a household's kids or an elderly relative may tap, not a mouse-driven desktop app. Automated tooling like axe-core catches roughly half of real accessibility issues by volume per its own documentation; this is a floor, not a certification — real screen-reader and keyboard-only testing still hasn't been done.

Two honest limits. The fixtures are hand-written to match the bridge's real payloads (`tau_core/web/server.py`, ui-bridge's resources) — **if the bridge's shapes drift, the screenshots become a lie**, so keep them in step. And headless Chromium has no usable microphone, so the harness removes the capture APIs and drives the text-input fallback; the voice path and the green listening hue aren't covered here (see the next section for what does cover the voice path).

## Voice behavior check

```bash
npm test                        # pure-logic unit tests (wake/sleep-phrase matching, no browser)
npm run voice-behavior-check    # real-browser state-machine tests (Playwright)
```

Unlike the visual check, this one **is** an assertion suite over the voice-invoke state machine (`useVoiceInvoke.js`): half-duplex gating, bounded conversational follow-up, wake-word-gated barge-in, explicit/stop-phrase closers, and the tightened wake-phrase match (see the plan's Phase 16 §8.17 for the full write-up). `scripts/test-voice-match.mjs` covers the pure string-matching logic (`src/utils/voiceMatch.js`) with plain Node, no framework. `scripts/voice-behavior-check.mjs` drives the real production build in real Chromium: a fake `SpeechRecognition` class is injected before the app's own code runs, so the app's actual production logic reacts to scripted transcripts exactly as it would a real recognizer, and every assertion is on real, observable output (DOM state, the actual network calls made) - not internals. It always builds its own bundle with `VITE_VOICE_MODE=browser` in a separate `--outDir`, so a local `.env` set to `local` mode can't silently turn every scenario into a no-op.

**Not covered:** local mode's MediaRecorder+VAD capture path (would need Chromium's fake-audio-device flags and a synthetic source to drive realistically) and real hardware/timing feel - these tests prove the state machine is correct, not that it feels right on an actual microphone.

## Known gaps / future work

- `SystemDrawer` doesn't yet auto-close on state changes (e.g. lockdown clearing) - it's fully user-controlled for now.
- The visual check covers six states in two themes at one viewport (1280x800). Portrait/narrow layouts and real older-iPad hardware are still unverified - `--viewport` takes any size if you want to add more. The voice path's *state machine* is now covered by `voice-behavior-check.mjs` above; real-hardware/real-timing feel is not.
- The karaoke sweep is a reading-cadence approximation, not real TTS alignment (see "Visual design") - it will drift against actual audio on a long reply.
- The neuron field's resolution is limited by what the audit feed carries: it shows *which server* is being called, not the model's internal phase. "Routing" and "recall" aren't separate neurons because the bridge publishes no turn-phase events - recall shows up only as a `memory-mcp-server` call. A first-class per-device phase stream from the bridge is the upgrade path.
- `RecognitionCard` keeps its own enter animation rather than using `DrawIn`, and `DesignDrawing` keeps its per-element `stroke-dashoffset` trace - stacking a wipe on top of either would muddy an animation that already works.
- No scrollback on the kiosk by design (6.D/6.F): only the latest exchange is recallable, and older turns are only readable in the admin dashboard's unified log. If a household ever wants per-device scrollback, the device-scoped history is already in `ui-bridge-mcp-server` - it's a UI decision, not a missing capability.
- The 6.C second pass is still open: the whole-UI "drawn" SVG-stroke reveal and the neuron-style active-state canvas.
