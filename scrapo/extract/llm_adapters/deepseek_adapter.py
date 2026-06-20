"""DeepSeek adapter — OpenAI-compatible chat completions in JSON-object mode.

DeepSeek speaks the OpenAI wire protocol, so we reuse the ``openai`` async client
pointed at DeepSeek's base URL. The one meaningful difference from
:class:`~scrapo.extract.llm_adapters.openai_adapter.OpenAIAdapter`: DeepSeek
supports JSON *object* mode (``response_format={"type": "json_object"}``) but not
OpenAI's strict ``json_schema`` response format, so the target schema is conveyed
through the prompt (which the hybrid extractor already does) rather than enforced
by the API.

Configuration (all env-overridable, no config.py change needed):

* ``DEEPSEEK_API_KEY``     — credential (required to make a call)
* ``SCRAPO_DEEPSEEK_MODEL`` — model id, default ``deepseek-v4-flash``
* ``DEEPSEEK_BASE_URL``    — API base, default ``https://api.deepseek.com``

Select it with ``SCRAPO_LLM_ADAPTER=deepseek``.
"""

from __future__ import annotations

import json
import os
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse

_DEFAULT_BASE_URL = "https://api.deepseek.com"
# deepseek-v4-flash is the current default; deepseek-chat / deepseek-reasoner are
# deprecated (2026/07/24) and map to its non-thinking / thinking modes.
_DEFAULT_MODEL = "deepseek-v4-flash"

# USD per 1M tokens, (input cache-miss, output). Published list prices; DeepSeek
# also offers a much cheaper cache-hit input rate and off-peak discounts we do not
# model here (so this is a conservative upper bound). The fallback keeps an unknown
# model (e.g. a newer release set via SCRAPO_DEEPSEEK_MODEL) from silently $0.
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-chat": (0.14, 0.28),  # → v4-flash non-thinking
    "deepseek-reasoner": (0.14, 0.28),  # → v4-flash thinking
}
_FALLBACK_RATE: tuple[float, float] = (0.14, 0.28)


class DeepSeekAdapter:
    provider = "deepseek"

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI  # DeepSeek is OpenAI-protocol-compatible
        except ImportError as e:
            raise ImportError(
                "Install scrapo[openai] (the OpenAI client) to use the DeepSeek adapter"
            ) from e
        self.model_id = model_id or os.environ.get("SCRAPO_DEEPSEEK_MODEL", _DEFAULT_MODEL)
        self._client = AsyncOpenAI(
            api_key=api_key or os.environ.get("DEEPSEEK_API_KEY"),
            base_url=base_url or os.environ.get("DEEPSEEK_BASE_URL", _DEFAULT_BASE_URL),
        )

    async def extract_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.model_id,
            "messages": [
                # DeepSeek's JSON mode requires the word "json" to appear in the
                # input; this system line satisfies that and steers the output.
                {"role": "system", "content": "Return ONLY valid JSON for the given schema."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
        }
        if schema is not None:
            # JSON-object mode (DeepSeek does not support strict json_schema). The
            # schema shape is carried by the prompt the hybrid extractor builds.
            kwargs["response_format"] = {"type": "json_object"}
        resp = await self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        json_payload: dict[str, Any] | list[Any] | None
        try:
            json_payload = json.loads(text)
        except json.JSONDecodeError:
            json_payload = None
        usage = resp.usage
        in_tok = (getattr(usage, "prompt_tokens", 0) if usage else 0) or 0
        out_tok = (getattr(usage, "completion_tokens", 0) if usage else 0) or 0
        return LLMResponse(
            text=text,
            json_payload=json_payload,
            provider=self.provider,
            model_id=self.model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=self._cost(in_tok, out_tok),
        )

    def _cost(self, in_tok: int, out_tok: int) -> float:
        in_price, out_price = _PRICING_USD_PER_MTOK.get(self.model_id, _FALLBACK_RATE)
        return (in_tok / 1_000_000.0) * in_price + (out_tok / 1_000_000.0) * out_price
