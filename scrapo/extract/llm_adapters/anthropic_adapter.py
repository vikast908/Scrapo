"""Anthropic Claude adapter — primary supported LLM provider.

Uses prompt caching on the schema/system block so repeated extractions against
the same Pydantic schema are cheap. Cost calculation uses published per-MTok
prices for Opus 4.7 and Sonnet 4.6 — adjust if pricing changes.
"""

from __future__ import annotations

import json
import os
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse

_PRICING_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.0, 75.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}


class AnthropicAdapter:
    provider = "anthropic"

    def __init__(self, model_id: str | None = None, api_key: str | None = None) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as e:
            raise ImportError(
                "Install scrapo[anthropic] to use the Claude adapter"
            ) from e
        self.model_id = model_id or os.environ.get("SCRAPO_LLM_MODEL", "claude-opus-4-7")
        self._client = AsyncAnthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY"))

    async def extract_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        system_blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "You are a precise web-page data extractor. "
                    "Read the page content and return ONLY valid JSON conforming to the given schema. "
                    "Do not include any commentary, explanations, or markdown fences. "
                    "If a field cannot be located, use null."
                ),
            }
        ]
        if schema is not None:
            system_blocks.append(
                {
                    "type": "text",
                    "text": "Target JSON schema:\n" + json.dumps(schema, separators=(",", ":")),
                    "cache_control": {"type": "ephemeral"},
                }
            )

        resp = await self._client.messages.create(
            model=self.model_id,
            max_tokens=max_tokens,
            system=system_blocks,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text"
        )
        json_payload: dict[str, Any] | list[Any] | None = None
        try:
            json_payload = json.loads(_strip_fences(text))
        except json.JSONDecodeError:
            json_payload = None

        in_tok = getattr(resp.usage, "input_tokens", 0) or 0
        out_tok = getattr(resp.usage, "output_tokens", 0) or 0
        # Prompt-cache tokens are billed at non-standard multiples of the input
        # rate: cache WRITES at ~1.25x input, cache READS at ~0.1x input. The
        # schema block carries cache_control=ephemeral, so on repeat extractions
        # these dominate and ignoring them under-reports cost.
        cache_creation_tok = getattr(resp.usage, "cache_creation_input_tokens", 0) or 0
        cache_read_tok = getattr(resp.usage, "cache_read_input_tokens", 0) or 0
        cost = self._cost(in_tok, out_tok, cache_creation_tok, cache_read_tok)
        return LLMResponse(
            text=text,
            json_payload=json_payload,
            provider=self.provider,
            model_id=self.model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
        )

    def _cost(
        self,
        in_tok: int,
        out_tok: int,
        cache_creation_tok: int = 0,
        cache_read_tok: int = 0,
    ) -> float:
        prices = _PRICING_USD_PER_MTOK.get(self.model_id)
        if not prices:
            return 0.0
        in_price, out_price = prices
        return (
            (in_tok / 1_000_000.0) * in_price
            + (out_tok / 1_000_000.0) * out_price
            # cache writes ~1.25x input rate, cache reads ~0.10x input rate
            + (cache_creation_tok / 1_000_000.0) * in_price * 1.25
            + (cache_read_tok / 1_000_000.0) * in_price * 0.10
        )


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```", 2)
        text = parts[1] if len(parts) >= 2 else ""
        if text.startswith("json"):
            text = text[4:]
    # Strip a trailing fence even when the model leaves whitespace before it:
    # without rstrip(), `endswith("```")` is False on `"...\n```"` and the fence
    # leaks into json.loads.
    text = text.rstrip()
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return text.strip()
