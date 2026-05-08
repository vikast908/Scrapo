"""Common LLM adapter protocol + factory."""

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


def get_default(provider: str | None = None, model_id: str | None = None) -> LLMAdapter:
    """Return the best available adapter, or a deterministic mock as last resort."""
    provider = provider or os.environ.get("SCRAPO_LLM_ADAPTER", "anthropic")

    if provider == "anthropic":
        try:
            from scrapo.extract.llm_adapters.anthropic_adapter import AnthropicAdapter

            return AnthropicAdapter(model_id=model_id)
        except ImportError:
            pass
    if provider == "openai":
        try:
            from scrapo.extract.llm_adapters.openai_adapter import OpenAIAdapter

            return OpenAIAdapter(model_id=model_id)
        except ImportError:
            pass
    if provider == "gemini":
        try:
            from scrapo.extract.llm_adapters.gemini_adapter import GeminiAdapter

            return GeminiAdapter(model_id=model_id)
        except ImportError:
            pass

    from scrapo.extract.llm_adapters.mock_adapter import MockAdapter

    return MockAdapter()
