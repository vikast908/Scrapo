"""DeepSeek adapter — a preset of the generic OpenAI-compatible adapter.

DeepSeek speaks the OpenAI wire protocol, so this just points the generic adapter
at ``https://api.deepseek.com`` with DeepSeek's pricing and default model. Select
with ``SCRAPO_LLM_ADAPTER=deepseek``; configure via ``DEEPSEEK_API_KEY``,
``SCRAPO_DEEPSEEK_MODEL`` (default ``deepseek-v4-flash``), ``DEEPSEEK_BASE_URL``.
"""

from __future__ import annotations

import os

from scrapo.extract.llm_adapters.openai_compatible import OpenAICompatibleAdapter

_DEFAULT_BASE_URL = "https://api.deepseek.com"
# deepseek-v4-flash is the current default; deepseek-chat / deepseek-reasoner are
# deprecated (2026/07/24) and map to its non-thinking / thinking modes.
_DEFAULT_MODEL = "deepseek-v4-flash"

# USD per 1M tokens, (input cache-miss, output). Published list prices; the cheaper
# cache-hit input rate and off-peak discounts are not modelled (conservative).
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-chat": (0.14, 0.28),  # -> v4-flash non-thinking
    "deepseek-reasoner": (0.14, 0.28),  # -> v4-flash thinking
}


class DeepSeekAdapter(OpenAICompatibleAdapter):
    provider = "deepseek"
    default_model = _DEFAULT_MODEL
    _pricing = _PRICING_USD_PER_MTOK
    _fallback_rate = (0.14, 0.28)

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(
            model_id=model_id or os.environ.get("SCRAPO_DEEPSEEK_MODEL"),
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_BASE_URL),
            provider="deepseek",
            default_model=_DEFAULT_MODEL,
        )
