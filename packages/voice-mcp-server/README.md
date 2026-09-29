# voice-mcp-server

The voice MCP server for Project Tau — see `project-tau-plan.md`
Section 5.3. Phase 2's second domain server: wraps Piper (TTS) and faster-whisper (STT) as MCP
tools. Independently deployable, like every domain server in Tau's architecture — this package
does not depend on `tau-core`.

## Layout

```
src/voice_mcp_server/
  whisper_client.py   WhisperClient - httpx multipart POST to faster-whisper-server's
                       OpenAI-compatible /v1/audio/transcriptions endpoint
  piper_client.py      PiperClient - Wyoming-protocol TTS round trip to Piper, returns WAV bytes
  server.py            FastMCP server: tool definitions, .env loading
tests/
  test_whisper_client.py   WhisperClient against httpx.MockTransport - no real network
  test_piper_client.py     PiperClient against a real local asyncio TCP server speaking raw
                           Wyoming frames - exercises the actual wire protocol, no real Piper
  test_server.py           tool functions called directly, with a monkeypatched client seam
```

## Tools

- `speak(text) -> {"audio_base64": ..., "format": "wav"}` — synthesizes speech via Piper.
  Audio is base64-encoded since MCP tool results are JSON.
- `transcribe(audio_base64, filename="audio.wav") -> str` — transcribes base64-encoded audio via
  Whisper.
- `enroll_voiceprint(person_id, audio_base64)` — compute voice embedding and enroll for speaker ID.
  **Requires human approval** — enrollment is security-relevant. Returns embedding ready to pass to
  `memory-mcp-server.store_voice(person_id, embedding)`.
- `identify_speaker(audio_base64, threshold=0.7)` — compute voice embedding from incoming audio.
  Read-only operation: returns embedding ready to pass to `memory-mcp-server.match_voice(embedding)`
  for similarity search against enrolled voiceprints.
- `detect_wake_word(audio_base64) -> {detected, score, model, threshold}` — on-device wake-word
  detection via openWakeWord over a short audio window. Read-only; the bridge polls it in local
  voice mode so hands-free "hey ..." invoke works without a cloud recognizer. See "Local wake
  word" below.
- `list_voices() -> [{id, label, current, available}]` — the voices Tau can speak in, the active one
  flagged and each marked with whether the TTS service actually has that model on disk. Read-only.
- `set_voice(voice_id)` — switch the spoken voice. **Requires human approval** — see "Selectable TTS
  voices" below.

**CDG gating, and what falls outside it** — `speak`/`transcribe`/`identify_speaker`/
`detect_wake_word`/`list_wake_words`/`list_voices` are output or read-only actions with no physical
or destructive effect, so they fall through `default_effect: allow` and need no rule. The two
*settings* tools are different: `set_wake_word` and `set_voice` each change behaviour on every
device in the household, so both carry a `require_approval` rule in
`tau-core/config/cdg_rules.yaml`. `enroll_voiceprint` is gated because it writes biometric data.

## Selectable TTS voices

Tau's spoken voice is switchable at runtime, the same way the wake word is — same registry shape,
same persistence, same approval gate. `tts_voice.py` is a deliberate mirror of `wake_word.py`.

- **The registry** is `AVAILABLE_VOICES` in `src/voice_mcp_server/tts_voice.py`. A voice id *is* its
  Piper voice name (`en_US-amy-medium`) — the same string is the compose `--voice` flag, the
  `SynthesizeVoice.name` on the wire, the `.onnx` filename, and the HuggingFace directory leaf.
- **`PIPER_VOICE` only sets the first-run default.** Once someone picks a voice through the Admin
  Dashboard's VOICE panel, that choice persists to `/app/data/tts_voice_selection.json` on the
  `voice-models` volume and outranks the env var. A container can't write back to its own
  environment, so without that file every redeploy would silently revert a deliberate choice.
- **The constraint worth knowing:** wyoming-piper only downloads the one voice named in its own
  `--voice` startup flag. Everything else has to be put on the `piper-data` volume first, which is
  what `docker-compose.yml`'s `piper-voice-seed` service does on first boot (idempotent; a voice it
  can't fetch logs a warning rather than blocking the stack).
- **If a selected voice has no model**, `speak()` retries once with no voice at all, so Piper falls
  back to its startup voice. Speaking is best-effort by contract all the way up to
  `/api/voice/speak` — a missing optional voice degrades to "Tau still talks, in the default voice",
  never to silence. `list_voices()` reports `available: false` for such a voice so the picker can
  say so up front instead of letting someone pick one and quietly get a different one.

To add a voice: add an entry to `AVAILABLE_VOICES` (with its `hf_dir`), add the matching
`dir:name` line to the seed service's `specs` list in `docker-compose.yml`, and restart the stack.

## Speaker-ID model notes (hard-won on Windows, 2026-07-12)

`enroll_voiceprint`/`identify_speaker` lazy-load speechbrain's `spkrec-ecapa-voxceleb`
(192-dim ECAPA embeddings). Three portability lessons are baked into `voice_embedding.py`:

1. **Import `speechbrain.inference.speaker`, never the legacy `speechbrain.pretrained`** - the
   deprecated redirect lazy-imports optional integrations (k2, not installable on Windows) and
   the failure mode is a recursive-inspect *hang* inside the MCP stdio server, not an error.
2. **`LocalStrategy.COPY`, not the default SYMLINK** - symlink creation on Windows needs
   elevation/Developer Mode (WinError 1314). Model cache dir: `~/.cache/tau/spkrec-ecapa-voxceleb`,
   override with `SPKREC_MODEL_DIR`.
3. **Audio decode via PyAV, not torchaudio** - torchaudio 2.13+ `load()` requires the separate
   torchcodec plugin and can't decode the browser's webm/opus clips regardless; PyAV (bundled
   FFmpeg, same stack as the local faster-whisper service) handles WAV/webm/mp4 alike.

Runtime deps for these two tools: `pip install speechbrain torch av numpy` (CPU torch is fine;
see infra note about the multi-GB download). `speak`/`transcribe` need none of this.

**GPU:** the embedding runs on CPU by default; set `SPKREC_DEVICE=cuda` to use a
passed-through GPU (needs CUDA-enabled torch wheels in the image - see the GPU section of the
repo-root README / `docker-compose.gpu.yml`). It's a tiny model, so CPU is perfectly usable;
GPU mainly matters when it shares a box with Ollama/Whisper under load.

### Calibrating the match threshold

`identify_speaker` accepts the nearest enrolled voiceprint only within
`TAU_VOICE_MATCH_MAX_DISTANCE` (Chroma L2, lower = closer). The shipped default (0.75) is
**uncalibrated** — access tiers are only as trustworthy as this ceiling, so calibrate before
relying on them. `scripts/calibrate_threshold.py` measures same-speaker vs different-speaker
distance distributions and recommends a ceiling:

```bash
# quick synthetic pass (Windows SAPI voices - David/Zira/...), zero setup:
python scripts/calibrate_threshold.py --synthesize
# real voices: a dir of "<speaker>__<label>.wav" clips, >=2 speakers x >=2 clips:
python scripts/calibrate_threshold.py --audio-dir ./voice-samples
```

It prints both an Equal-Error-Rate (balanced) and a security-biased ceiling (largest distance
keeping false-accepts under `--far-target`, default 1%); take the security-biased one, since the
system is meant to fail toward "unknown = lowest access." The selection math lives in
`voice_mcp_server.calibration` and is unit-tested (`tests/test_calibration.py`) independently of
the embedding stack. Synthetic SAPI voices are a sanity pass only — real household mics/room
acoustics are noisier, so record the final ceiling from real enrolled voices.

### Local wake word

`detect_wake_word` runs [openWakeWord](https://github.com/dscripka/openWakeWord) on-device (ONNX,
MIT-licensed) so hands-free invoke works in the fully-local voice mode without Chrome's cloud
recognizer. Install the extra: `pip install -e '.[wake-word]'` then fetch the bundled models once
(`python -c "import openwakeword; openwakeword.utils.download_models()"`). The frontend's
`useWakeWord` hook (local mode) records short rolling windows and posts them to the bridge's
`POST /api/voice/wake`, firing the same invoke path as an atom double-tap on `detected`.

#### The bundled "hey tau" model

`models/hey_tau.onnx` (209KB) ships with this package and is the default `WAKEWORD_MODEL`. It was
trained locally against openWakeWord's pipeline; `WAKEWORD_THRESHOLD` (default 0.5) picks the
operating point.

#### Switching the wake word at runtime (2026-09-08)

`WAKEWORD_MODEL` only sets the *first-run* default. To change it live - no restart, no
training - use the `list_wake_words`/`set_wake_word` MCP tools (bridge: `GET`/`POST
/api/voice/wake-words`, surfaced as the Settings picker in the Admin Dashboard's WAKE WORD
panel). Both are backed by `AVAILABLE_WAKE_WORDS` in `wake_word.py`: this project's own trained
`hey_tau`, plus openWakeWord's bundled `hey_jarvis`, `alexa`, `hey_mycroft`, and `hey_rhasspy` -
confirmed against the actual `.onnx` files shipped in the installed package, not the library's
full demo set (which also bundles non-wake-word models like `timer`/`weather` that detect those
spoken words generally, not an invocation phrase - deliberately excluded).

`set_wake_word` is CDG-gated (`require_approval` in `cdg_rules.yaml`) - switching it changes what
*every* device in the household listens for, not a private per-device setting, so even an admin
using the Settings picker gets a pending-approval card first, same as voice enrollment. The choice
persists to `/app/data/wake_word_selection.json` on the `voice-models` volume, so it survives a
container restart or redeploy without needing a compose edit - and takes priority over
`WAKEWORD_MODEL` once set, since it represents a more recent, explicit decision.

**Recipe** (RTX 3070, ~1h wall clock end to end):

| Ingredient | What |
|---|---|
| Positives | 50,000 synthetic utterances + 2,000 held out, Piper `en_US-libritts_r-medium` (~900 speakers) |
| Target phrases | `hey tau` **and** `hey taw` — see below, this matters |
| Negatives | 50,000 phoneme-neighbour utterances + 2,000 held out, mined by DeepPhonemizer plus 38 hand-written confusables |
| Augmentation | 270 MIT room impulse responses + 2,000 AudioSet background clips, random SNR |
| Negative features | ACAV100M, 2,000 hours (16GB precomputed) |
| Validation | 10.7h speech/noise/music, held out |

**Why two target spellings.** espeak-ng renders `tau` as /tˈaʊ/ (rhymes with "now") and only that.
Piper phonemizes deterministically, so all ~900 LibriTTS speakers would say the identical vowel —
speaker diversity buys zero *pronunciation* diversity. A model trained on `hey tau` alone would
never fire for someone who says /tɔː/. Adding the spelling `hey taw` (→ /tˈɔː/) covers both
readings in one binary model. Confirmed independently by DeepPhonemizer: `tau → [T][AW]`,
`taw → [T][AO]`.

**Measured operating curve** (2,000 held-out positives *after* room/noise augmentation; 2,000
held-out phoneme neighbours; 10.7h continuous stream):

| `WAKEWORD_THRESHOLD` | recall | adversarial false-accept | false wakes/hour |
|---|---|---|---|
| 0.20 | 51.5% | 0.7% | 1.78 |
| 0.30 | 46.1% | 0.5% | 0.93 |
| 0.40 | 40.6% | 0.4% | 0.47 |
| **0.50** (default) | **36.4%** | **0.2%** | **0.37** |
| 0.60 | 31.5% | 0.2% | 0.19 |

**Be honest about what this is.** It is a genuine on-device "hey tau" detector, and it is *not*
good enough to be the primary hands-free path — at the default threshold roughly two out of three
utterances are missed. Double-tap remains the reliable route; treat the wake word as a
convenience. Notably the model is *not* confused by its phoneme neighbours ("hey town", "hey now",
"hey how" all sit at ≤0.7% false-accept) — it under-fires rather than over-triggers, so the
limiting factor is positive-class sensitivity, not phrase confusability.

**If you want to improve it**, in rough order of expected payoff: record real "hey tau" utterances
from the actual household mics and mix them into the positive set (synthetic TTS is the binding
constraint — the model has never heard a real human say it); raise `augmentation_rounds`; or
switch to a longer wake phrase, since two-syllable words are intrinsically harder to detect
reliably than three.

One trap worth recording: openWakeWord's `auto_train` doubles `max_negative_weight` after any
sequence whose validation false-positive rate exceeds `target_false_positives_per_hour`. With the
documented defaults that escalated 1500 → 6000 and produced a model with 14.9% recall and a
*perfect* 0.0 false-positive rate — it had simply learned not to fire. Keeping the weight flat at
100 (by setting an unreachable fp target) tripled recall and moved the median positive score from
0.044 to 0.224. Let the deployment threshold set the operating point, not the training weights.

Honest tradeoff: detection is
polled at ~1–2s cadence and rides the audited `host.call_tool` path like `transcribe`, so it adds
a steady trickle to the audit log; if that becomes noise, the clean fix is a standalone service
(the `infra/whisper` pattern) the frontend hits directly. The detector's decision logic is
unit-tested (`tests/test_wake_word.py`) with openWakeWord/PyAV faked; end-to-end detection needs
the real model and real-device mic testing.

## Backends

Both already have infra scaffolding (code-only, not yet applied — see
[`../infra/README.md`](../../infra/README.md)):

| Service | Protocol | Image | infra manifest |
|---|---|---|---|
| Whisper (STT) | OpenAI-compatible REST, port 8000 | `fedirz/faster-whisper-server` | `infra/k3s/voice/whisper-deployment.yaml` |
| Piper (TTS) | Wyoming (JSON-header + binary payload over TCP), port 10200 | `rhasspy/wyoming-piper` | `infra/k3s/voice/piper-deployment.yaml` |

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # .venv/bin/activate on Linux/macOS
pip install -e ".[dev]"
cp .env.example .env           # fill in WHISPER_URL / PIPER_URI, never commit .env
pytest
```

## Wiring into tau-core

Registered in `tau-core/config/servers.yaml` as `voice-mcp-server`, spawned via
`python -m voice_mcp_server.server`. Needs this package installed
(`pip install -e ../voice-mcp-server`) into whatever environment `tau-core` runs
`command: python` with — simplest for local dev is tau-core's own `.venv`.

Like `home-assistant-mcp-server`, `WHISPER_URL`/`PIPER_URI` are read lazily per tool call, not at
process startup — the server starts and lists its tools fine with no backend running; only
calling a tool without them set returns a clear `RuntimeError`.

## Manual smoke test (once Whisper/Piper are actually running)

Either apply `infra/k3s/voice/` to a real cluster, or run both images locally via Docker
(`docker run -p 8000:8000 fedirz/faster-whisper-server:latest-cpu`,
`docker run -p 10200:10200 rhasspy/wyoming-piper --voice en_US-lessac-medium`), then connect the
same way `tau-core/tests/test_mcp_client_manager.py` does for the `echo` example server, using
`tau_core.mcp_client.MCPClientManager` with a `ServerConfig` pointing at
`python -m voice_mcp_server.server`.
