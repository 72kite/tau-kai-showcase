"""Tests for build_main_model - the model/fallback construction pulled out of
TauAssistant.from_settings in Phase 35.

Worth knowing why these exist: from_settings had no test coverage at all (only examples/chat_repl.py
ever called it), so the four-way fallback nesting below had never been asserted anywhere. Extracting
the function is what made it testable, and the nesting order is load-bearing - vLLM first because
it's the fastest local path, Ollama as the local floor, and the paid remote only after both local
options have failed. Getting that order wrong would silently route every turn through OpenRouter.
"""

from __future__ import annotations

import logging

from pydantic_ai.models.fallback import FallbackModel

from tau_core.config import TauCoreSettings
from tau_core.llm.models import build_main_model


class FakeSecrets:
    """Dict-backed SecretsProvider. Mirrors EnvFileSecretsProvider's contract: get returns None
    for a missing key, require raises."""

    def __init__(self, **values: str):
        self._values = values

    def get(self, key: str) -> str | None:
        return self._values.get(key)

    def require(self, key: str) -> str:
        if key not in self._values:
            raise KeyError(key)
        return self._values[key]


def _base(**extra: str) -> FakeSecrets:
    return FakeSecrets(OLLAMA_HOST="http://localhost:11434", OLLAMA_MODEL="qwen2.5:7b", **extra)


def test_ollama_only_returns_a_plain_model() -> None:
    model = build_main_model(TauCoreSettings(), _base())
    assert not isinstance(model, FallbackModel)


def test_openrouter_configured_wraps_ollama_first_remote_second() -> None:
    model = build_main_model(
        TauCoreSettings(),
        _base(OPENROUTER_API_KEY="sk-test", OPENROUTER_MODEL="anthropic/claude-sonnet-4"),
    )
    assert isinstance(model, FallbackModel)
    # Local first, paid remote only as the fallback - never the other way round.
    assert not isinstance(model.models[0], FallbackModel)
    assert len(model.models) == 2


def test_vllm_configured_takes_the_primary_slot() -> None:
    model = build_main_model(
        TauCoreSettings(), _base(VLLM_HOST="http://vllm:8000", VLLM_MODEL="qwen2.5-7b-instruct")
    )
    assert isinstance(model, FallbackModel)
    assert len(model.models) == 2


def test_both_fallbacks_nest_vllm_ollama_then_openrouter() -> None:
    """The one assertion that pins the whole ordering: FallbackModel(FallbackModel(vllm, ollama),
    openrouter). Nothing asserted this before Phase 35."""
    model = build_main_model(
        TauCoreSettings(),
        _base(
            VLLM_HOST="http://vllm:8000",
            VLLM_MODEL="qwen2.5-7b-instruct",
            OPENROUTER_API_KEY="sk-test",
            OPENROUTER_MODEL="anthropic/claude-sonnet-4",
        ),
    )
    assert isinstance(model, FallbackModel)
    inner = model.models[0]
    assert isinstance(inner, FallbackModel), "vLLM+Ollama should be nested inside the remote fallback"
    assert len(inner.models) == 2


def test_half_configured_vllm_is_skipped_with_a_warning(caplog) -> None:
    """A stray VLLM_HOST with no VLLM_MODEL must not half-enable anything. The all-or-nothing gate
    is deliberate; the warning is what stops it being silent."""
    with caplog.at_level(logging.WARNING, logger="tau_core.llm.models"):
        model = build_main_model(TauCoreSettings(), _base(VLLM_HOST="http://vllm:8000"))
    assert not isinstance(model, FallbackModel)
    assert "VLLM_HOST and VLLM_MODEL must both be set" in caplog.text


def test_half_configured_openrouter_is_skipped_with_a_warning(caplog) -> None:
    """Same gate on the paid path, where getting it wrong costs money rather than just confusion:
    a key with no model is skipped rather than guessing a model and billing the operator."""
    with caplog.at_level(logging.WARNING, logger="tau_core.llm.models"):
        model = build_main_model(TauCoreSettings(), _base(OPENROUTER_API_KEY="sk-test"))
    assert not isinstance(model, FallbackModel)
    assert "OPENROUTER_API_KEY and OPENROUTER_MODEL must both be set" in caplog.text
