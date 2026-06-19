"""Gemini adapter."""

from __future__ import annotations

import json
import os
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse

# USD per 1M tokens, (input, output). Published list prices.
# gemini-2.5-flash standard tier: $0.30 input / $2.50 output per 1M tokens
# (Google's published price for prompts <=200k tokens). We use the lower
# documented input figure; revise if Google changes pricing.
_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-1.5-flash": (0.075, 0.30),
}
# Fallback so a known token count never silently costs $0. Mirrors the default
# model (gemini-2.5-flash).
_FALLBACK_RATE: tuple[float, float] = (0.30, 2.50)


class GeminiAdapter:
    provider = "gemini"

    def __init__(self, model_id: str | None = None, api_key: str | None = None) -> None:
        try:
            from google import genai  # type: ignore[attr-defined]
        except ImportError as e:
            raise ImportError("Install scrapo[gemini] to use the Gemini adapter") from e
        self.model_id = model_id or os.environ.get("SCRAPO_GEMINI_MODEL", "gemini-2.5-flash")
        self._client = genai.Client(api_key=api_key or os.environ.get("GEMINI_API_KEY"))

    async def extract_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        cfg: dict[str, Any] = {"max_output_tokens": max_tokens}
        if schema is not None:
            cfg["response_mime_type"] = "application/json"
            cfg["response_schema"] = schema
        resp = await self._client.aio.models.generate_content(
            model=self.model_id, contents=prompt, config=cfg
        )
        text = getattr(resp, "text", "") or ""
        json_payload: dict[str, Any] | list[Any] | None
        try:
            json_payload = json.loads(text)
        except json.JSONDecodeError:
            json_payload = None
        # The google-genai SDK exposes token counts on response.usage_metadata
        # (prompt_token_count / candidates_token_count). Read them best-effort:
        # the field may be absent on some response shapes, so guard with getattr.
        usage = getattr(resp, "usage_metadata", None)
        in_tok = (getattr(usage, "prompt_token_count", 0) if usage else 0) or 0
        out_tok = (getattr(usage, "candidates_token_count", 0) if usage else 0) or 0
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
