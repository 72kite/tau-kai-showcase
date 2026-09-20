"""Threshold-calibration math (voice_mcp_server.calibration). Pure numpy, no embedding stack."""

import numpy as np
import pytest

from voice_mcp_server.calibration import (
    l2_distance,
    pairwise_distances,
    recommend_threshold,
)


def test_l2_distance_matches_norm():
    assert l2_distance([0, 0], [3, 4]) == pytest.approx(5.0)


def test_pairwise_splits_genuine_from_impostor():
    # Two speakers, two clips each; same-speaker clips identical, cross-speaker far apart.
    embs = {
        "a": [[0.0, 0.0], [0.0, 0.0]],
        "b": [[10.0, 0.0], [10.0, 0.0]],
    }
    genuine, impostor = pairwise_distances(embs)
    # genuine: a-a and b-b (2 pairs), impostor: the 4 a-b cross pairs.
    assert sorted(genuine) == [0.0, 0.0]
    assert impostor == [10.0, 10.0, 10.0, 10.0]


def test_recommend_threshold_separable_case():
    genuine = [0.1, 0.2, 0.15, 0.25]     # all small
    impostor = [1.0, 1.2, 0.9, 1.5]      # all large
    r = recommend_threshold(genuine, impostor, far_target=0.01)
    assert r.separable is True
    # A clean gap exists between 0.25 and 0.9, so both thresholds land inside it with zero error.
    assert 0.25 <= r.secure_threshold <= 0.9
    assert 0.25 <= r.eer_threshold <= 0.9
    assert r.secure_far == 0.0
    assert r.secure_frr == 0.0
    assert r.eer == pytest.approx(0.0)


def test_recommend_threshold_overlapping_case_reports_nonzero_error():
    # Deliberate overlap: some impostor pairs are closer than some genuine pairs.
    genuine = [0.2, 0.4, 0.9]
    impostor = [0.5, 0.6, 0.3]
    r = recommend_threshold(genuine, impostor, far_target=0.01)
    assert r.separable is False
    assert r.eer > 0.0  # cannot achieve zero error when the classes overlap


def test_security_bias_never_exceeds_far_target_when_achievable():
    genuine = [0.1, 0.2]
    impostor = [0.8, 0.9, 1.0]
    r = recommend_threshold(genuine, impostor, far_target=0.0)
    # With a 0% FAR budget the secure threshold must admit no impostor.
    assert r.secure_far == 0.0
    assert r.secure_threshold < min(impostor)


def test_empty_class_is_an_error():
    with pytest.raises(ValueError):
        recommend_threshold([], [1.0, 2.0])
    with pytest.raises(ValueError):
        recommend_threshold([0.1], [])


def test_summary_mentions_the_env_var():
    r = recommend_threshold([0.1, 0.2], [1.0, 1.1])
    assert "TAU_VOICE_MATCH_MAX_DISTANCE" in r.summary()
