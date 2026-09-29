import React, { useCallback, useEffect, useRef, useState } from 'react'
import Atom from './components/Atom'
import BootScreen from './components/BootScreen'
import DesignDrawing from './components/DesignDrawing'
import TranscriptionOverlay from './components/TranscriptionOverlay'
import ApprovalQueue from './components/ApprovalQueue'
import RecognitionCard from './components/RecognitionCard'
import SystemDrawer from './components/SystemDrawer'
import TopBar from './components/TopBar'
import ModelActivity from './components/ModelActivity'
import StatusFooter from './components/StatusFooter'
import ConnectionSettings from './components/ConnectionSettings'
import DataLink from './components/DataLink'
import { isTauri } from './utils/tauri'
import { hasApiBaseOverride } from './hooks/useMCPResource'
import { useDeviceState } from './hooks/useDeviceState'
import { useDeviceId } from './hooks/useDeviceId'
import { useApprovals } from './hooks/useApprovals'
import { usePeople } from './hooks/usePeople'
import { useDraftCount } from './hooks/useDraftCount'
import { useDesktopNotifications } from './hooks/useDesktopNotifications'
import { useKioskMode } from './hooks/useKioskMode'
import { useChat } from './hooks/useChat'
import { useSpeech } from './hooks/useSpeech'
import { useVoiceInvoke } from './hooks/useVoiceInvoke'
import { useWakeWord } from './hooks/useWakeWord'
import { useMicLevel } from './hooks/useMicLevel'
import './App.css'

// 10MB per file, matching the bridge's MAX_ATTACHMENT_B64_CHARS guard.
const MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
const MAX_ATTACHMENTS_PER_TURN = 5

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      // result is a data: URL; the bridge wants bare base64 after the comma.
      const commaAt = String(reader.result).indexOf(',')
      resolve(String(reader.result).slice(commaAt + 1))
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

const EMPTY_STATE = {
  transcription: { active: false, current_text: '', history: [] },
  security: { lockdown_active: false, intrusion_count: 0 },
  devices: { device_count: 0, online_count: 0 },
  vision: { camera_active: false, scene_description: '' },
  design: { active: false, title: '', description: '', svg: '', ascii_art: '' },
  recognition: { active: false, person_id: '', role: '', svg: '', ascii_art: '', recognized_at: '' },
}

// How long the response stays up after a reply before auto-hiding (Phase 6.F raised this from
// 10s: the karaoke line takes longer to read than the old log did to skim).
const AUTO_HIDE_MS = 30000

// Phase 19 barge-in default (per-device, persisted; this is just the first-run value). **Off by
// default as of 2026-08-19** - it was shipped on under the assumption that echoCancellation
// (audioConstraints.js) reliably cancels Tau's own TTS output, so the wake-word listener staying
// live during playback would only ever hear a genuine interruption. That assumption doesn't hold
// on real kiosk hardware (one mic, one speaker, no hardware AEC): `echoCancellation: true` is a
// *request* to the browser, not a guarantee, and on this hardware it wasn't enough - the listener
// heard Tau's own reply, "barged in" on itself, and produced another reply, which could do the
// same thing again. Tap-to-interrupt (tapping the atom while Tau is speaking) still works
// regardless of this setting - it isn't audio-based, so it isn't exposed to the same failure.
// VITE_BARGE_IN_DEFAULT=true ships it back on for a kiosk with hardware AEC good enough to trust.
const BARGE_IN_DEFAULT =
  String(import.meta.env.VITE_BARGE_IN_DEFAULT ?? 'false').toLowerCase() === 'true'
// v2: renamed from 'tau-barge-in' so already-provisioned kiosks pick up the new default above
// instead of keeping whatever the old on-by-default first run persisted - the whole point of this
// change is that the old default was unsafe, and a lot of currently-affected kiosks already have
// '1' written under the old key from that very first run.
const BARGE_IN_KEY = 'tau-barge-in-v2'

/**
 * Top-level layout (Phase 6.F). All chrome is consolidated into one top toolbar (TopBar), so the
 * atom owns the center of the stage by default. When Tau replies, the atom slides aside *within
 * its own box* and the response surface takes the space next to it - the atom never unmounts,
 * never stops animating, and never gets replaced; it moves. The response auto-hides after
 * AUTO_HIDE_MS and can be recalled by tapping the atom's box, since per 6.D the durable record
 * belongs in the admin-only unified log rather than on a kiosk screen.
 *
 * The design panel only takes half the stage while there's an active design (ui://design); with
 * nothing to draw, the atom gets the whole stage rather than sharing it with a dashboard that
 * duplicated the toolbar.
 */
export default function App() {
  useKioskMode()

  // Phase 27.D: the Tauri desktop shell has no same-origin tau-core to fall back to, so first
  // launch needs the user to say where tau-core actually is before anything else tries to talk to
  // it. The kiosk build never sees this - isTauri() is false there, full stop. Computed once on
  // mount (a lazy initializer, not re-checked every render) since ConnectionSettings' onSaved
  // callback is what flips it, not a poll.
  const [needsConnectionSetup, setNeedsConnectionSetup] = useState(
    () => isTauri() && !hasApiBaseOverride()
  )
  const [booting, setBooting] = useState(true)

  // Phase 6.D: this device registers itself and polls its OWN transcript, never the shared one.
  // The identity is consumed by StatusFooter so the screen can say which device it is.
  const { deviceId, name: deviceName } = useDeviceId()
  const { data: uiState } = useDeviceState()
  const { approvals, approve, deny, challenge, speakChallenge, cancelChallenge } = useApprovals()
  const { send } = useChat()
  // Phase 13 voice-out: reads Tau's short reply aloud after a VOICE-initiated turn. `muted` is a
  // per-device preference (persisted), surfaced as a toggle in the toolbar.
  // Phase 17 follow-up: the moment a spoken reply finishes, open the bounded bare-utterance window.
  // A ref indirection because armFollowup comes from useVoiceInvoke below, which needs handleCommand
  // (defined below) - so useSpeech can't receive it directly at call time.
  const armFollowupRef = useRef(() => {})
  const {
    speak,
    stop: stopSpeech,
    muted: voiceMuted,
    toggleMuted: toggleVoiceMute,
    speaking,
    speakingRef,
  } = useSpeech({ onPlaybackEnd: () => armFollowupRef.current?.() })

  // Phase 19 barge-in: per-device on/off, persisted like the mute toggle. When on, the wake phrase
  // interrupts a spoken reply (below).
  const [bargeInEnabled, setBargeInEnabled] = useState(() => {
    const saved = localStorage.getItem(BARGE_IN_KEY)
    return saved === null ? BARGE_IN_DEFAULT : saved === '1'
  })
  useEffect(() => {
    localStorage.setItem(BARGE_IN_KEY, bargeInEnabled ? '1' : '0')
  }, [bargeInEnabled])
  const toggleBargeIn = useCallback(() => setBargeInEnabled((v) => !v), [])
  const { people, error: peopleError } = usePeople()
  // Badges the ADMIN entry when Tau has written notes nobody has reviewed (Phase 9).
  const draftCount = useDraftCount()
  // Phase 27.D Milestone 4: native OS notifications for new pending approvals/drafts - a no-op on
  // the kiosk build (isTauri() false), closing the "someone has to walk past a kiosk" gap
  // useDraftCount.js's own docstring names, now that a real desktop client exists.
  useDesktopNotifications(approvals, draftCount)

  const [drawerOpen, setDrawerOpen] = useState(false)
  const [responseShown, setResponseShown] = useState(false)
  const [manualText, setManualText] = useState('')
  const [dragActive, setDragActive] = useState(false)
  const [attachNote, setAttachNote] = useState('')
  const hideTimerRef = useRef(null)
  // Attachments staged for the next command (drop or attach button). Sent with the next chat
  // turn - voice, atom-click, or typed - then cleared.
  const pendingAttachmentsRef = useRef([])
  const fileInputRef = useRef(null)

  const state = uiState || EMPTY_STATE
  const complexity =
    state.devices?.device_count > 0
      ? state.devices.online_count / state.devices.device_count
      : 0.5

  // Phase 40 "visual answer cards": the reply's picture (e.g. research-mcp-server.search_images
  // via spawn_subagent), if this turn's tool calls produced one - {url, title, source_url,
  // source} or null. Local component state, not part of the polled transcript: unlike reply
  // TEXT, which every device sees via ui-bridge's mirrored history, the image only ever reaches
  // the device that actually made this /api/chat call. Cleared on every new command so a stale
  // picture from an earlier turn doesn't linger next to an unrelated reply.
  const [lastImage, setLastImage] = useState(null)

  // The two ends of the retrieval data-link (Phase 54). `atomNodeAnchorRef` is written by the
  // atom's frame loop with the live viewport position of one orbiting node; `imageFrameRef` is
  // the framed picture the link runs to. Both are refs rather than state so the 60fps writer
  // never re-renders this component - DataLink samples them on its own slow schedule.
  const atomNodeAnchorRef = useRef(null)
  const imageFrameRef = useRef(null)

  const handleCommand = useCallback(
    async (text, speaker = null, source = 'voice') => {
      const attachments = pendingAttachmentsRef.current
      pendingAttachmentsRef.current = []
      setAttachNote('')
      setLastImage(null)
      try {
        const result = await send(text, attachments, speaker)
        // Voice in -> voice out: read the reply aloud only when the turn came in by voice, so a
        // typed question stays silent. Best-effort (useSpeech degrades if TTS is unavailable).
        if (source === 'voice' && result?.reply) speak(result.reply)
        setLastImage(result?.image ?? null)
      } catch {
        // useChat already captured the error; the transcript/UI just won't show a reply for
        // this turn. Nothing further to do here - we don't want a failed turn to throw past
        // useVoiceInvoke's handleCommand, which would leave `mode` stuck off 'idle'.
      }
    },
    [send, speak]
  )

  const {
    mode,
    supported,
    invoke,
    cancel: cancelVoice,
    submitCommand,
    armFollowup,
    followUpSecondsLeft,
    error: voiceError,
    voiceStatus,
  } = useVoiceInvoke({
    onCommand: handleCommand,
    // Half-duplex gate (Phase 16 #1): the recognizer drops what it hears while Tau is speaking,
    // so its own voice-out can't re-trigger the wake phrase / be captured as a command - except
    // for a wake-word-gated barge-in, which the hook itself allows through when bargeInEnabled.
    speakingRef,
    // An atom tap (or local-mode wake word) while Tau is speaking always barges in - see invoke()
    // in the hook itself.
    stopSpeaking: stopSpeech,
    // Phase 19 barge-in (browser mode): the wake phrase mid-reply interrupts. onBargeIn stops
    // playback (which also drops the speaking gate) so capture can open.
    bargeInEnabled,
    onBargeIn: stopSpeech,
  })

  // Phase 19 barge-in (local mode): the wake loop is normally suspended while Tau speaks; with
  // barge-in on it stays live so a spoken interruption can be heard, and a detection mid-reply
  // stops playback before opening capture. This depends on echoCancellation actually cancelling
  // Tau's own TTS output - not guaranteed on real kiosk hardware, see BARGE_IN_DEFAULT above for
  // why that's now off by default rather than assumed safe.
  const handleWake = useCallback(() => {
    if (speakingRef.current) stopSpeech()
    invoke()
  }, [speakingRef, stopSpeech, invoke])

  // Keep the ref useSpeech calls pointed at the live armFollowup.
  useEffect(() => {
    armFollowupRef.current = armFollowup
  }, [armFollowup])

  // Fully-local hands-free wake word (VITE_VOICE_MODE=local only; no-op otherwise). Listens for
  // the wake phrase on-device via /api/voice/wake -> openWakeWord and fires the same invoke()
  // the atom double-tap uses. Disabled while a turn is in progress so it yields the mic during
  // command capture and TTS playback.
  // `!speaking` is the local-mode half of the half-duplex gate (Phase 16 #1): because speak() is
  // fire-and-forget, `mode` is back to 'idle' while Tau's reply plays, so without this the
  // wake-word loop would re-arm and record the speaker's own output. Phase 19: when barge-in is
  // on, the loop is allowed to stay live during playback (`!speaking || bargeInEnabled`) so the
  // wake phrase can interrupt - relying on echoCancellation to keep Tau's own voice from
  // self-triggering it, which is exactly why barge-in defaults off now (see BARGE_IN_DEFAULT).
  useWakeWord({
    onWake: handleWake,
    enabled: mode === 'idle' && (!speaking || bargeInEnabled),
  })

  // Local Web Audio amplitude while capturing a command (wake-triggered OR a bounded follow-up
  // continuation) - drives the atom's outer-ring voice reaction. A ref, not state: read inside
  // the Three.js frame loop.
  const micLevelRef = useMicLevel(mode === 'invoked' || mode === 'followup')

  const historyLen = state.transcription?.history?.length ?? 0
  const hasHistory = historyLen > 0
  const prevHistoryLen = useRef(historyLen)

  // Show the response surface on invoke/thinking, and whenever a new line lands - a reply usually
  // arrives *after* the turn has already dropped back to idle (the turn completes, then the next
  // poll delivers the text), so keying on mode alone would let it flash past.
  useEffect(() => {
    const grew = historyLen > prevHistoryLen.current
    prevHistoryLen.current = historyLen
    if (mode !== 'idle' || grew) setResponseShown(true)
  }, [mode, historyLen])

  // Auto-hide once things go quiet. Deliberately a separate effect keyed on responseShown, so the
  // 30s window starts when the surface actually goes up rather than on mount - the atom must be
  // centered and alone at rest, not sharing the stage with an empty panel.
  useEffect(() => {
    if (!responseShown || mode !== 'idle') return undefined
    hideTimerRef.current = setTimeout(() => setResponseShown(false), AUTO_HIDE_MS)
    return () => clearTimeout(hideTimerRef.current)
  }, [responseShown, mode, historyLen])

  const handleAtomClick = () => {
    invoke()
  }

  // 6.F: tapping the atom's box (anywhere but the atom itself, which invokes) brings the last
  // response back after it has auto-hidden. Nothing to recall if nothing was ever said.
  // Explicit closer (Phase 16 next slice): the same tap now doubles as tap-anywhere-to-stop when
  // there's something active to stop - halts playback if Tau is speaking, or cancels an
  // in-progress capture (wake-triggered or follow-up) without submitting whatever was heard so
  // far. Falls through to the recall behavior only when neither applies.
  const handleStageClick = () => {
    if (speaking) {
      // `stopSpeech`, not `stop`. This read `stop()` until 2026-09-22, and the bug was invisible
      // because it did not throw: there is no local binding called `stop` (useSpeech's is
      // destructured as `stop: stopSpeech`), so the call resolved to the global `window.stop()` -
      // a real DOM method that aborts page loading and has nothing to do with speech. So
      // tap-anywhere-to-stop silently did nothing to a reply in progress, with no console error
      // to notice, while the two OTHER interrupt paths (atom double-tap via useVoiceInvoke's
      // stopSpeaking, and wake-word barge-in via onBargeIn) both wired `stopSpeech` correctly and
      // worked - which is why this looked functional.
      stopSpeech()
      return
    }
    if (mode === 'invoked' || mode === 'followup') {
      cancelVoice()
      return
    }
    if (!responseShown && hasHistory) setResponseShown(true)
  }

  // The atom only slides aside when there is genuinely something to make room for. Without this
  // guard the surface's own empty state would hold the stage open and leave the atom off-centre.
  const surfaceOpen = responseShown && (hasHistory || mode === 'thinking')

  // Files arrive by drag-drop anywhere on the app, or via the ATTACH button's picker. Either
  // way they're staged, then a chat turn is sent immediately - with the typed text if the
  // manual input has any, else a default "analyze this" command. Extraction (PDF text, image
  // description) happens server-side in the bridge, so every client behaves identically.
  const stageAndSendFiles = useCallback(
    async (fileList) => {
      const files = Array.from(fileList || []).slice(0, MAX_ATTACHMENTS_PER_TURN)
      if (files.length === 0) return
      const oversized = files.find((f) => f.size > MAX_ATTACHMENT_BYTES)
      if (oversized) {
        setAttachNote(`${oversized.name} is over the 10MB limit`)
        return
      }
      setAttachNote(`Attaching ${files.map((f) => f.name).join(', ')}…`)
      try {
        const attachments = await Promise.all(
          files.map(async (file) => ({
            name: file.name,
            content_type: file.type || '',
            data_b64: await readFileAsBase64(file),
          }))
        )
        pendingAttachmentsRef.current = attachments
        const commandText = manualText.trim() || 'Analyze the attached file(s).'
        setManualText('')
        submitCommand(commandText)
      } catch (e) {
        pendingAttachmentsRef.current = []
        setAttachNote(`Could not read file: ${e.message}`)
      }
    },
    [manualText, submitCommand]
  )

  const handleDrop = (e) => {
    e.preventDefault()
    setDragActive(false)
    stageAndSendFiles(e.dataTransfer?.files)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    if (!dragActive) setDragActive(true)
  }

  const handleDragLeave = (e) => {
    // Only clear when leaving the window/root, not when crossing into a child element.
    if (e.target === e.currentTarget) setDragActive(false)
  }

  // Split from the event handler deliberately (Phase 50): a real, headed-browser Enter keypress
  // on .manual-invoke-input was confirmed NOT to reach this logic reliably via the form's native
  // onSubmit - the browser logs "Form submission canceled because the form is not connected",
  // and neither a real Enter nor a real click on the SEND button (also type="submit", same native
  // path) invoked it within a 2s window. The form has no `action`, so a genuinely trusted native
  // Enter/click has the browser's own implicit-submit default to race against React's synchronous
  // state update - this isn't a headless/CDP testing artifact, it reproduced in a real focused
  // browser window too. Fixed by handling Enter/click explicitly and calling preventDefault()
  // before the browser ever queues its native submit, rather than relying on onSubmit to win a
  // race it demonstrably sometimes loses.
  const submitManualCommand = () => {
    if (!manualText.trim()) return
    submitCommand(manualText)
    setManualText('')
  }

  const handleManualKeyDown = (e) => {
    if (e.key !== 'Enter') return
    e.preventDefault()
    submitManualCommand()
  }

  const handleManualSubmitClick = (e) => {
    e.preventDefault()
    submitManualCommand()
  }

  if (booting) {
    return <BootScreen onDone={() => setBooting(false)} />
  }

  if (needsConnectionSetup) {
    return (
      <div className="app connection-gate">
        <h1>TAU</h1>
        <p>Where's tau-core running?</p>
        <ConnectionSettings onSaved={() => setNeedsConnectionSetup(false)} />
      </div>
    )
  }

  return (
    <div
      className="app"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
      <TopBar
        uiState={state}
        approvalCount={approvals.length}
        peopleCount={people.length}
        micSupported={supported}
        micStatus={voiceStatus}
        micLive={mode === 'invoked' || mode === 'followup'}
        voiceMuted={voiceMuted}
        onToggleVoiceMute={toggleVoiceMute}
        bargeInEnabled={bargeInEnabled}
        onToggleBargeIn={toggleBargeIn}
        speaking={speaking}
        onOpenDrawer={() => setDrawerOpen(true)}
        onAttach={() => fileInputRef.current?.click()}
      />

      {dragActive && (
        <div className="drop-overlay">
          <span>DROP FILE FOR TAU</span>
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        multiple
        accept=".pdf,.png,.jpg,.jpeg,.webp,.txt,.md,.json,.csv,.yaml,.yml,.log"
        style={{ display: 'none' }}
        onChange={(e) => {
          stageAndSendFiles(e.target.files)
          e.target.value = ''
        }}
      />

      <ApprovalQueue
        pendingApprovals={approvals}
        onApprove={approve}
        onDeny={deny}
        challenge={challenge}
        onSpeakChallenge={speakChallenge}
        onCancelChallenge={cancelChallenge}
      />

      <RecognitionCard recognition={state.recognition} />

      <SystemDrawer
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        uiState={state}
        people={people}
        peopleError={peopleError}
      />

      {/* The stage: the atom, centered and dominant, sharing the width only when there's an
          active design to draw. */}
      <div className={`main-stage ${state.design?.active ? 'with-design' : ''}`}>
        {/* The atom's own box. The atom is centered inside it and slides aside - within this same
            box - when there's a response to show beside it. Clicking the box (but not the atom,
            which invokes) recalls a response that has auto-hidden. */}
        <div
          className={`atom-stage ${surfaceOpen ? 'with-response' : ''} ${
            mode === 'invoked' ? 'sensor-listening' : ''
          } ${mode === 'followup' ? 'sensor-followup' : ''} ${
            state.vision?.camera_active ? 'sensor-vision' : ''
          }`}
          onClick={handleStageClick}
        >
          <div className="atom-column">
            <Atom
              complexity={complexity}
              isListening={mode === 'invoked' || mode === 'followup'}
              isThinking={mode === 'thinking'}
              voiceLevelRef={micLevelRef}
              nodeAnchorRef={atomNodeAnchorRef}
              onClick={handleAtomClick}
            />
            <ModelActivity mode={mode} followUpSecondsLeft={followUpSecondsLeft} />
          </div>

          <TranscriptionOverlay
            state={state.transcription}
            visible={surfaceOpen}
            working={mode === 'thinking'}
            image={lastImage}
            imageFrameRef={imageFrameRef}
          />

          {!surfaceOpen && hasHistory && (
            <button type="button" className="recall-hint" onClick={handleStageClick}>
              LAST REPLY
            </button>
          )}

          {!supported && mode === 'invoked' && (
            // onSubmit is a no-op safety net, not the real path (see submitManualCommand above) -
            // Enter and the SEND button both call preventDefault() and submit explicitly before
            // the browser's native implicit-submit default ever gets a chance to fire.
            <form className="manual-invoke-form" onSubmit={(e) => e.preventDefault()}>
              <input
                type="text"
                autoFocus
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                onKeyDown={handleManualKeyDown}
                placeholder={
                  voiceStatus === 'insecure'
                    ? 'Voice needs a secure (HTTPS) connection — type your command'
                    : "Voice input isn't supported on this browser — type your command"
                }
                className="manual-invoke-input"
              />
              <button type="button" className="manual-invoke-submit" onClick={handleManualSubmitClick}>
                SEND
              </button>
            </form>
          )}
        </div>

        {state.design?.active && <DesignDrawing design={state.design} />}
      </div>

      {/* Drawn only while a retrieved picture is actually on screen - the link exists to explain
          where that picture came from, so with no picture there is nothing for it to say. */}
      <DataLink
        anchorRef={atomNodeAnchorRef}
        targetRef={imageFrameRef}
        active={Boolean(lastImage?.url) && surfaceOpen}
      />

      {/* Far-bottom: which build this is, and which device you're standing in front of. */}
      <StatusFooter deviceId={deviceId} deviceName={deviceName} />

      {attachNote && <div className="attach-note">{attachNote}</div>}

      {voiceError && <div className="voice-error-toast">Voice error: {voiceError}</div>}
    </div>
  )
}
