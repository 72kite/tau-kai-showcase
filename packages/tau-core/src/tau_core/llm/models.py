from __future__ import annotations

import logging

import httpx
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.models import Model
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from tau_core.config import SecretsProvider, TauCoreSettings

logger = logging.getLogger(__name__)

# A local Ollama generating on a spilled-to-CPU model can legitimately take a long time to
# produce a first token, so this ceiling is generous - it exists to bound a *hung* backend, not
# to police a slow one. Without it there is no bound at all: the pre-Phase-7 provider was built
# with httpx's defaults overridden away, so a wedged Ollama meant a POST /api/chat that never
# returned and a request that never freed its worker.
DEFAULT_LLM_TIMEOUT_SECONDS = 120.0
# Connecting to Ollama is either fast or it's not listening; no reason to wait the full budget.
DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS = 10.0


def build_ollama_model(
    host: str,
    model_name: str,
    timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
) -> OpenAIChatModel:
    """Builds a PydanticAI model pointed at Ollama's OpenAI-compatible endpoint.

    Ollama ignores the API key but the OpenAI client requires one to be set.

    `timeout` bounds each HTTP request to Ollama (read/write/pool), with a shorter separate
    connect timeout. Callers that wrap a whole turn should still bound that separately - one
    turn can make several model requests, so a per-request timeout is not a turn timeout.

    **Phase 24 tried PydanticAI's OllamaProvider/OllamaModel here and reverted it — measured
    worse. Recorded so it is not re-tried on the same reasoning.**

    The hypothesis was good on paper. Both providers talk to the same `/v1/chat/completions`
    endpoint, so the swap changes no wire protocol; what it changes is the model PROFILE, and the
    OpenAI profile really is wrong for a local Qwen in two documented ways - it leaves
    `openai_supports_strict_tool_definition` at True (Ollama does not support strict mode,
    pydantic-ai #4116) and rewrites schemas with `OpenAIJsonSchemaTransformer`, which keeps
    `$defs`/`$ref` where `qwen_model_profile` would inline them. Verified live that the swap did
    change all three profile fields.

    It still did not work. On the 37-case suite, same model, same prompts, scoping on, one
    variable changed:

        OpenAI profile   27/37    13 JSON leaks
        Ollama profile   23/37    12 JSON leaks

    The leak count is the important number: eliminating malformed tool-call emission was the
    entire predicted mechanism, and 13 -> 12 is no effect. The score moved four cases the wrong
    way (one gain: `call_service` finally fired for "dim the lounge lamps"; four losses across
    memory, security and robotics), and retry loops appeared - 12 tool calls on one case, 9 and 8
    on others.

    So the garbage-token prefix (`IGHL торр {"name": ...}`) is NOT caused by the schema
    transformer or by strict-mode tool definitions. Whatever it is, this is not it. The thing that
    actually moved that failure mode was cutting the number of tools in context - see
    TauCoreSettings.scope_servers_per_turn, 13/37 -> 27/37.
    """
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS)
    )
    provider = OpenAIProvider(base_url=f"{host.rstrip('/')}/v1", api_key="ollama", http_client=http_client)
    return OpenAIChatModel(model_name, provider=provider)


def build_vllm_model(
    host: str,
    model_name: str,
    api_key: str = "EMPTY",
    timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
) -> OpenAIChatModel:
    """Builds a PydanticAI model pointed at vLLM's OpenAI-compatible endpoint.

    vLLM's server (`vllm serve ...`) speaks the same `/v1/chat/completions` wire protocol as
    Ollama and needs no api key by default - "EMPTY" is vLLM's own documented placeholder,
    the OpenAI client just requires the field to be non-empty. `model_name` must match what the
    server was started with (its `--served-model-name`, or the model path if that wasn't set).

    Unlike `build_ollama_model`, this does not need a non-default model profile: vLLM's
    OpenAI-compatible endpoint follows the strict-tool-definition and JSON-schema conventions the
    default OpenAI profile already assumes, so there's no equivalent of the Ollama-profile
    revert documented there.
    """
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS)
    )
    provider = OpenAIProvider(base_url=f"{host.rstrip('/')}/v1", api_key=api_key, http_client=http_client)
    return OpenAIChatModel(model_name, provider=provider)


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def build_openrouter_model(
    model_name: str,
    api_key: str,
    timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS,
) -> OpenAIChatModel:
    """Builds a PydanticAI model pointed at OpenRouter's OpenAI-compatible endpoint.

    Phase 10: an opt-in paid fallback for when the local Ollama is down or under-powered - see
    TauAssistant.from_settings, which wraps this with build_ollama_model in a FallbackModel.
    """
    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=DEFAULT_LLM_CONNECT_TIMEOUT_SECONDS)
    )
    provider = OpenAIProvider(base_url=OPENROUTER_BASE_URL, api_key=api_key, http_client=http_client)
    return OpenAIChatModel(model_name, provider=provider)


def build_main_model(settings: TauCoreSettings, secrets: SecretsProvider) -> Model:
    """Assembles the main reply model, with whichever fallbacks are configured layered on.

    Split out of TauAssistant.from_settings in Phase 35. Not a pure move: from_settings now calls
    secrets.require("OLLAMA_HOST") a second time for the router. That is free for
    EnvFileSecretsProvider (a dict/env lookup with no caching and no side effects), but a
    remote-backed SecretsProvider would pay for a second round trip - worth knowing if one is
    ever added.

    Nesting when everything is configured is FallbackModel(FallbackModel(vllm, ollama),
    openrouter): vLLM first because it is the fastest local path, Ollama as the always-present
    local floor, and the paid remote only once both local options have failed.
    """
    timeout = settings.llm_request_timeout_seconds
    main_model: Model = build_ollama_model(
        secrets.require("OLLAMA_HOST"), secrets.require("OLLAMA_MODEL"), timeout=timeout
    )

    # Opt-in vLLM primary: when configured, the main reply model tries vLLM first and falls back
    # to the Ollama model above on a vLLM API error (down, OOM, wrong served model name). Both
    # VLLM_HOST and VLLM_MODEL must be set to activate, same all-or-nothing gate as the OpenRouter
    # fallback below - a stray VLLM_HOST with no VLLM_MODEL would otherwise silently do nothing.
    # The router stays Ollama-only: it's a small fast confidence check that runs on every turn,
    # not a workload vLLM's heavier serving stack needs to take on.
    vllm_host = secrets.get("VLLM_HOST")
    vllm_model_name = secrets.get("VLLM_MODEL")
    if vllm_host and vllm_model_name:
        vllm_model = build_vllm_model(
            vllm_host, vllm_model_name, secrets.get("VLLM_API_KEY") or "EMPTY", timeout=timeout
        )
        main_model = FallbackModel(vllm_model, main_model, fallback_on=(ModelAPIError,))
    elif vllm_host or vllm_model_name:
        logger.warning(
            "VLLM_HOST and VLLM_MODEL must both be set to enable the vLLM primary - only one "
            "was found, so it stays disabled."
        )

    # Phase 10: opt-in paid fallback for when local Ollama is down or under-powered. Both
    # OPENROUTER_API_KEY and OPENROUTER_MODEL must be set to activate - a key without a model is
    # skipped rather than guessing one and surprising the operator with a bill. Only the main
    # reply model gets a fallback, not the router: that one runs a confidence check on every turn,
    # and OpenRouter costs real money per token, so the blast radius stays on the one call that
    # actually needs it.
    openrouter_key = secrets.get("OPENROUTER_API_KEY")
    openrouter_model_name = secrets.get("OPENROUTER_MODEL")
    if openrouter_key and openrouter_model_name:
        fallback_model = build_openrouter_model(
            openrouter_model_name, openrouter_key, timeout=timeout
        )
        main_model = FallbackModel(main_model, fallback_model, fallback_on=(ModelAPIError,))
    elif openrouter_key or openrouter_model_name:
        logger.warning(
            "OPENROUTER_API_KEY and OPENROUTER_MODEL must both be set to enable the "
            "OpenRouter fallback - only one was found, so it stays disabled."
        )

    return main_model
