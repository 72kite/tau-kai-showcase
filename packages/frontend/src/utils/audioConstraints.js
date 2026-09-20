// Shared getUserMedia audio constraints for every mic capture in the app (Phase 16 #2).
//
// `echoCancellation` is the one that matters for Tau: it's a half-duplex device (one mic, one
// speaker, often inches apart on a kiosk). Without AEC the speaker playing Tau's TTS bleeds
// straight into the mic, so the wake-word listener and command capture hear Tau talking to
// itself - the root of the "unintended interruptions" during a reply. `noiseSuppression` and
// `autoGainControl` further steady the signal for openWakeWord / faster-whisper.
//
// Browser support varies and these are best-effort hints, not guarantees - a browser that
// ignores them just behaves as before. This does NOT cover the browser SpeechRecognition path
// (Chrome owns that mic and takes no constraints); that path is gated in software instead, via
// the `speaking` flag from useSpeech (Phase 16 #1).
export const MIC_AUDIO_CONSTRAINTS = {
  echoCancellation: true,
  noiseSuppression: true,
  autoGainControl: true,
}
