"""Calibrate TAU_VOICE_MATCH_MAX_DISTANCE against real or synthesized enrolled voices.

project-tau-plan.md ships the match ceiling uncalibrated (default 0.75) with an explicit "do not
trust tiers before calibrating" warning, and calls for "synthetic David/Zira first, real
household voices after." This is that harness. The threshold math lives in (and is unit-tested
via) voice_mcp_server.calibration; this script only produces the embeddings and prints a report.

Two input modes:

  # 1. Synthetic (Windows only): speak several phrases with multiple SAPI voices (David, Zira,
  #    ...) and calibrate against those. Zero setup, good for a first sanity pass.
  python scripts/calibrate_threshold.py --synthesize

  # 2. Real voices: point at a directory of WAV/webm clips named "<speaker>__<anything>.ext"
  #    (e.g. zion__hello.wav, mom__phrase2.wav). At least two speakers, two clips each.
  python scripts/calibrate_threshold.py --audio-dir ./voice-samples

Requires the same embedding stack the server uses: `pip install speechbrain torch av numpy`
(SPKREC_DEVICE=cuda to calibrate on GPU). --synthesize additionally needs pywin32 (Windows SAPI).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

from voice_mcp_server.calibration import (
    pairwise_distances,
    recommend_threshold,
)
from voice_mcp_server.voice_embedding import VoiceEmbedder

# A handful of short, varied phrases so genuine pairs reflect real intra-speaker variation
# (different words), not one phrase embedded twice.
PHRASES = [
    "how may I help you today",
    "lock the front door and arm the alarm",
    "what is the temperature in the living room",
    "show me the camera in the garage",
    "remind me to check the printer at noon",
]


def _synthesize_sapi(out_dir: Path) -> dict[str, list[Path]]:
    """Speak each phrase with each installed SAPI voice into WAV files. Returns
    {speaker: [wav, ...]}. Windows-only."""
    try:
        import win32com.client  # type: ignore
    except ImportError:
        sys.exit("--synthesize needs pywin32 (Windows SAPI). Run: pip install pywin32")

    engine = win32com.client.Dispatch("SAPI.SpVoice")
    voices = engine.GetVoices()
    result: dict[str, list[Path]] = {}
    for vi in range(voices.Count):
        voice = voices.Item(vi)
        # SAPI names look like "...Microsoft David Desktop - English"; take a short label.
        raw = voice.GetDescription()
        label = raw.split("Microsoft")[-1].split("Desktop")[0].strip() or f"voice{vi}"
        engine.Voice = voice
        clips: list[Path] = []
        for pi, phrase in enumerate(PHRASES):
            wav = out_dir / f"{label}__{pi}.wav"
            stream = win32com.client.Dispatch("SAPI.SpFileStream")
            stream.Open(str(wav), 3)  # 3 = SSFMCreateForWrite
            engine.AudioOutputStream = stream
            engine.Speak(phrase)
            stream.Close()
            clips.append(wav)
        result[label] = clips
        print(f"  synthesized {len(clips)} clips for voice '{label}'")
    engine.AudioOutputStream = None
    if len(result) < 2:
        sys.exit(f"need >=2 SAPI voices to calibrate; found {len(result)}. Add Windows voices.")
    return result


def _load_dir(audio_dir: Path) -> dict[str, list[Path]]:
    """Group '<speaker>__<anything>.<ext>' files by speaker."""
    result: dict[str, list[Path]] = {}
    for path in sorted(audio_dir.iterdir()):
        if path.suffix.lower() not in {".wav", ".webm", ".mp4", ".m4a", ".ogg"}:
            continue
        if "__" not in path.stem:
            print(f"  skipping {path.name}: expected '<speaker>__<label>.<ext>'")
            continue
        speaker = path.stem.split("__", 1)[0]
        result.setdefault(speaker, []).append(path)
    speakers = {s: c for s, c in result.items() if len(c) >= 2}
    if len(speakers) < 2:
        sys.exit(
            "need >=2 speakers with >=2 clips each. Name files '<speaker>__<label>.wav', e.g. "
            "zion__a.wav zion__b.wav mom__a.wav mom__b.wav"
        )
    return speakers


def _embed_all(clips_by_speaker: dict[str, list[Path]]) -> dict[str, list]:
    embedder = VoiceEmbedder()
    out: dict[str, list] = {}
    for speaker, clips in clips_by_speaker.items():
        embs = []
        for clip in clips:
            embs.append(embedder.compute_embedding(clip.read_bytes()))
        out[speaker] = embs
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--synthesize", action="store_true", help="use Windows SAPI voices")
    src.add_argument("--audio-dir", type=Path, help="dir of '<speaker>__<label>.wav' clips")
    ap.add_argument("--far-target", type=float, default=0.01,
                    help="max acceptable false-accept rate for the security-biased threshold")
    args = ap.parse_args()

    if args.synthesize:
        tmp = Path(tempfile.mkdtemp(prefix="tau-calib-"))
        print(f"Synthesizing SAPI voices into {tmp} ...")
        clips = _synthesize_sapi(tmp)
    else:
        print(f"Loading clips from {args.audio_dir} ...")
        clips = _load_dir(args.audio_dir)

    print(f"Computing embeddings for {sum(len(c) for c in clips.values())} clips "
          f"across {len(clips)} speakers (first run downloads the model)...")
    embeddings = _embed_all(clips)

    genuine, impostor = pairwise_distances(embeddings)
    result = recommend_threshold(genuine, impostor, far_target=args.far_target)
    print("\n" + result.summary())
    if not result.separable:
        print("\nNOTE: genuine and impostor distances OVERLAP - no threshold separates them "
              "cleanly. Expect false accepts/rejects; consider better mics or the challenge-"
              "response flow for anything elevated (which this system already does).")


if __name__ == "__main__":
    main()
