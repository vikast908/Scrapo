"""OpenAI adapter — a preset of the generic OpenAI-compatible adapter.

Uses OpenAI's default endpoint with OpenAI pricing. Select with
``SCRAPO_LLM_ADAPTER=openai``; configure via ``OPENAI_API_KEY`` and
``SCRAPO_OPENAI_MODEL`` (default ``gpt-4o-mini``).
"""

from __future__ import annotations

import os

from scrapo.extract.llm_adapters.openai_compatible import OpenAICompatibleAdapter

_DEFAULT_MODEL = "gpt-4o-mini"

# USD per 1M tokens, (input, output). Published list prices.
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
}


class OpenAIAdapter(OpenAICompatibleAdapter):
    provider = "openai"
    default_model = _DEFAULT_MODEL
    _pricing = _PRICING_USD_PER_MTOK
    _fallback_rate = (0.15, 0.60)

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            model_id=model_id or os.environ.get("SCRAPO_OPENAI_MODEL"),
            api_key=api_key or os.environ.get("OPENAI_API_KEY"),
            base_url=base_url,  # None -> the OpenAI default endpoint
            provider="openai",
            default_model=_DEFAULT_MODEL,
        )
