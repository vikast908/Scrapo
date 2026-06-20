"""Common LLM adapter protocol + model-agnostic factory.

Scrapo is provider-agnostic: it speaks to native SDKs for Anthropic and Gemini,
and to *any* OpenAI-wire-compatible endpoint (OpenAI, DeepSeek, OpenRouter,
Ollama, vLLM, LM Studio, Groq, Together, local gateways, ...) through one generic
adapter. Pick a provider with ``SCRAPO_LLM_ADAPTER``; if unset, the factory
auto-detects from whichever API key is present, falling back to a deterministic
mock so nothing breaks when no model is configured.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(slots=True)
class LLMResponse:
    text: str
    json_payload: dict[str, Any] | list[Any] | None
    provider: str
    model_id: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class LLMAdapter(Protocol):
    provider: str
    model_id: str

    async def extract_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse: ...


# Provider -> the env var whose presence signals "use me" during auto-detection,
# checked in this order. Local/self-hosted endpoints are opt-in (no key to sniff),
# so they are selected explicitly via SCRAPO_LLM_ADAPTER, not auto-detected.
_AUTODETECT: tuple[tuple[str, str], ...] = (
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
    ("deepseek", "DEEPSEEK_API_KEY"),
    ("openrouter", "OPENROUTER_API_KEY"),
    ("gemini", "GEMINI_API_KEY"),
    ("openai-compatible", "SCRAPO_LLM_BASE_URL"),
)


def _autodetect_provider() -> str:
    for provider, env in _AUTODETECT:
        if os.environ.get(env):
            return provider
    return "mock"


def get_default(provider: str | None = None, model_id: str | None = None) -> LLMAdapter:
    """Return the configured adapter (provider-agnostic), or a deterministic mock.

    Resolution order: explicit ``provider`` arg, then ``SCRAPO_LLM_ADAPTER``, then
    auto-detection by available API key, then the mock. A missing client library
    for the chosen provider also degrades to the mock rather than raising.
    """
    provider = (provider or os.environ.get("SCRAPO_LLM_ADAPTER") or "").strip().lower()
    if not provider:
        provider = _autodetect_provider()
    try:
        return _build(provider, model_id)
    except ImportError:
        from scrapo.extract.llm_adapters.mock_adapter import MockAdapter

        return MockAdapter()


def _build(provider: str, model_id: str | None) -> LLMAdapter:
    if provider == "anthropic":
        from scrapo.extract.llm_adapters.anthropic_adapter import AnthropicAdapter

        return AnthropicAdapter(model_id=model_id)
    if provider == "gemini":
        from scrapo.extract.llm_adapters.gemini_adapter import GeminiAdapter

        return GeminiAdapter(model_id=model_id)
    if provider == "openai":
        from scrapo.extract.llm_adapters.openai_adapter import OpenAIAdapter

        return OpenAIAdapter(model_id=model_id)
    if provider == "deepseek":
        from scrapo.extract.llm_adapters.deepseek_adapter import DeepSeekAdapter

        return DeepSeekAdapter(model_id=model_id)
    if provider == "openrouter":
        from scrapo.extract.llm_adapters.openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(
            model_id=model_id or os.environ.get("SCRAPO_OPENROUTER_MODEL"),
            api_key=os.environ.get("OPENROUTER_API_KEY"),
            base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            provider="openrouter",
            default_model="openai/gpt-4o-mini",
        )
    if provider == "ollama":
        from scrapo.extract.llm_adapters.openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(
            model_id=model_id or os.environ.get("SCRAPO_OLLAMA_MODEL"),
            api_key=None,
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            provider="ollama",
            default_model="llama3.1",
        )
    if provider in ("openai-compatible", "openai_compatible", "custom"):
        from scrapo.extract.llm_adapters.openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(
            model_id=model_id or os.environ.get("SCRAPO_LLM_MODEL"),
            api_key=os.environ.get("SCRAPO_LLM_API_KEY"),
            base_url=os.environ.get("SCRAPO_LLM_BASE_URL"),
            provider="openai-compatible",
        )

    from scrapo.extract.llm_adapters.mock_adapter import MockAdapter

    return MockAdapter()
