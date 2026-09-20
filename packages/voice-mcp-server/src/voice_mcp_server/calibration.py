"""Threshold calibration math for speaker verification.

`identify_speaker`/`match_voice` accept a match when the nearest enrolled voiceprint is within
`TAU_VOICE_MATCH_MAX_DISTANCE` (Chroma L2 distance - LOWER is closer). That ceiling was shipped
uncalibrated (default 0.75) with an explicit "do not trust tiers before calibrating" warning in
project-tau-plan.md. This module turns a set of enrolled recordings into a recommended ceiling.

The math here is pure (numpy only, no torch/audio), so it's unit-testable without the embedding
stack. scripts/calibrate_threshold.py is the runnable harness that produces the distance arrays
(by embedding real or synthesized speech) and feeds them in.

Given, for a labelled set of clips:
  - `genuine`  distances: pairs of clips from the SAME speaker (want these SMALL)
  - `impostor` distances: pairs of clips from DIFFERENT speakers (want these LARGE)
a threshold t accepts a pair when distance <= t. Then:
  - FRR(t) (false reject rate) = fraction of genuine pairs with distance  > t
  - FAR(t) (false accept  rate) = fraction of impostor pairs with distance <= t
We report the Equal Error Rate crossover (balanced) AND a security-biased ceiling (the largest t
whose FAR stays under a target), because this system is supposed to fail toward "unknown =
lowest access," so under-accepting impostors matters more than occasionally re-challenging a
genuine speaker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


@dataclass
class CalibrationResult:
    eer_threshold: float          # balanced FAR ~= FRR
    eer: float                    # the error rate at that crossover
    secure_threshold: float       # largest t with FAR <= far_target
    secure_far: float
    secure_frr: float
    far_target: float
    genuine_count: int
    impostor_count: int
    genuine_mean: float
    impostor_mean: float
    separable: bool               # True if max(genuine) < min(impostor): a clean gap exists

    def summary(self) -> str:
        gap = "clean gap" if self.separable else "OVERLAP (some impostor closer than some genuine)"
        return (
            f"genuine pairs: {self.genuine_count} (mean dist {self.genuine_mean:.3f})\n"
            f"impostor pairs: {self.impostor_count} (mean dist {self.impostor_mean:.3f})\n"
            f"distributions: {gap}\n"
            f"EER threshold: {self.eer_threshold:.3f}  (equal error rate {self.eer:.1%})\n"
            f"secure threshold: {self.secure_threshold:.3f}  "
            f"(FAR {self.secure_far:.1%}, FRR {self.secure_frr:.1%}, FAR target <= {self.far_target:.1%})\n"
            f"-> set TAU_VOICE_MATCH_MAX_DISTANCE={self.secure_threshold:.3f} "
            f"(security-biased) or {self.eer_threshold:.3f} (balanced)"
        )


def _far(impostor: np.ndarray, t: float) -> float:
    if impostor.size == 0:
        return 0.0
    return float(np.mean(impostor <= t))


def _frr(genuine: np.ndarray, t: float) -> float:
    if genuine.size == 0:
        return 0.0
    return float(np.mean(genuine > t))


def recommend_threshold(
    genuine_distances,
    impostor_distances,
    far_target: float = 0.01,
) -> CalibrationResult:
    """Recommend a match-distance ceiling from genuine/impostor distance samples.

    Raises ValueError if either sample is empty - you cannot calibrate a ceiling from one class.
    """
    genuine = np.asarray(list(genuine_distances), dtype=float)
    impostor = np.asarray(list(impostor_distances), dtype=float)
    if genuine.size == 0 or impostor.size == 0:
        raise ValueError("need at least one genuine and one impostor distance to calibrate")

    # Sweep every candidate threshold that could change a decision (each observed distance, plus
    # a point just above the max so FAR can reach its ceiling). Midpoints between adjacent
    # candidates avoid tie-at-boundary ambiguity.
    candidates = np.unique(np.concatenate([genuine, impostor]))
    lo, hi = float(candidates.min()), float(candidates.max())
    sweep = np.concatenate([[lo - 1e-6], (candidates[:-1] + candidates[1:]) / 2, [hi + 1e-6]])

    # EER crossover: threshold minimizing |FAR - FRR|.
    best_t, best_gap, best_eer = sweep[0], math.inf, 1.0
    for t in sweep:
        far, frr = _far(impostor, t), _frr(genuine, t)
        gap = abs(far - frr)
        if gap < best_gap:
            best_gap, best_t, best_eer = gap, float(t), (far + frr) / 2

    # Security-biased: the largest threshold whose FAR stays within target (accept as many
    # genuine as possible without letting impostors past the FAR budget). Falls back to the
    # strictest sweep point if even that exceeds the budget.
    secure_t = float(sweep[0])
    for t in sweep:
        if _far(impostor, t) <= far_target:
            secure_t = float(t)
        else:
            break

    return CalibrationResult(
        eer_threshold=best_t,
        eer=best_eer,
        secure_threshold=secure_t,
        secure_far=_far(impostor, secure_t),
        secure_frr=_frr(genuine, secure_t),
        far_target=far_target,
        genuine_count=int(genuine.size),
        impostor_count=int(impostor.size),
        genuine_mean=float(genuine.mean()),
        impostor_mean=float(impostor.mean()),
        separable=bool(genuine.max() < impostor.min()),
    )


def l2_distance(a, b) -> float:
    """Chroma reports squared/L2 distance for embeddings; match that here so calibrated numbers
    are directly comparable to what identify_speaker sees at runtime."""
    av, bv = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(np.linalg.norm(av - bv))


def pairwise_distances(labelled_embeddings: dict[str, list]) -> tuple[list[float], list[float]]:
    """Given {speaker_id: [embedding, ...]}, return (genuine_distances, impostor_distances) over
    all unordered clip pairs."""
    genuine: list[float] = []
    impostor: list[float] = []
    items = [(spk, emb) for spk, embs in labelled_embeddings.items() for emb in embs]
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (spk_a, emb_a), (spk_b, emb_b) = items[i], items[j]
            d = l2_distance(emb_a, emb_b)
            (genuine if spk_a == spk_b else impostor).append(d)
    return genuine, impostor
