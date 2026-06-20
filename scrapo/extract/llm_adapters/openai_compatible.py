"""Generic adapter for any OpenAI-wire-compatible chat endpoint.

A single adapter that talks to *any* server speaking the OpenAI chat-completions
protocol — OpenAI, DeepSeek, OpenRouter, Ollama, vLLM, LM Studio, Groq, Together,
local gateways, and so on — by varying only ``base_url`` + ``api_key`` + ``model``.
This is what makes Scrapo model-agnostic: bring whatever endpoint you like.

JSON handling is defensive. It asks for ``response_format={"type":"json_object"}``
(honoured by OpenAI/DeepSeek/OpenRouter and many others), but if the endpoint or
model rejects that parameter it transparently retries without it and relies on the
prompt — which already instructs the model to return a single JSON object — so
even bare local models work.
"""

from __future__ import annotations

import json
import os
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse


class OpenAICompatibleAdapter:
    """Talk to any OpenAI-compatible chat endpoint. Subclassed by per-provider
    presets (DeepSeek, OpenAI), or used directly for OpenRouter / Ollama / custom."""

    provider = "openai-compatible"
    default_model = "gpt-4o-mini"
    # USD per 1M tokens (input, output). Class-level so per-provider presets set
    # them as class attributes and cost accounting works even when an instance is
    # built without __init__ (e.g. in tests). Generic endpoints (OpenRouter, Ollama,
    # custom) have no fixed price list, so they cost 0 here.
    _pricing: dict[str, tuple[float, float]] = {}
    _fallback_rate: tuple[float, float] = (0.0, 0.0)

    def __init__(
        self,
        model_id: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        *,
        provider: str | None = None,
        default_model: str | None = None,
    ) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError(
                "Install scrapo[openai] (the OpenAI client) to use OpenAI-compatible adapters"
            ) from e
        if provider:
            self.provider = provider
        self.model_id = model_id or default_model or self.default_model
        # Local/self-hosted endpoints (Ollama, LM Studio, ...) need no key, but the
        # OpenAI client insists on a non-empty string, so pass a harmless placeholder.
        self._client = AsyncOpenAI(api_key=api_key or "not-required", base_url=base_url)

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
                {"role": "system", "content": "Return ONLY valid JSON for the requested schema."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
        }
        resp = await self._create(kwargs, want_json=schema is not None)
        text = (resp.choices[0].message.content or "") if resp.choices else ""
        json_payload: dict[str, Any] | list[Any] | None
        try:
            json_payload = json.loads(text)
        except json.JSONDecodeError:
            json_payload = None
        usage = getattr(resp, "usage", None)
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

    async def _create(self, kwargs: dict[str, Any], *, want_json: bool) -> Any:
        # JSON-object mode where supported; gracefully degrade to prompt-only JSON
        # for endpoints/models that reject response_format (many local models).
        if want_json:
            try:
                return await self._client.chat.completions.create(
                    **kwargs, response_format={"type": "json_object"}
                )
            except Exception:  # noqa: BLE001 - retry without the unsupported param
                pass
        return await self._client.chat.completions.create(**kwargs)

    def _cost(self, in_tok: int, out_tok: int) -> float:
        in_price, out_price = self._pricing.get(self.model_id, self._fallback_rate)
        return (in_tok / 1_000_000.0) * in_price + (out_tok / 1_000_000.0) * out_price
