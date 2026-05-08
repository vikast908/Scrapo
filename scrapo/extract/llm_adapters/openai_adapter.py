"""OpenAI adapter — uses Responses API with structured outputs when schema given."""

from __future__ import annotations

import json
import os
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse


class OpenAIAdapter:
    provider = "openai"

    def __init__(self, model_id: str | None = None, api_key: str | None = None) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("Install scrapo[openai] to use the OpenAI adapter") from e
        self.model_id = model_id or os.environ.get("SCRAPO_OPENAI_MODEL", "gpt-4o-mini")
        self._client = AsyncOpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))

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
                {"role": "system", "content": "Return ONLY valid JSON for the given schema."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
        }
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "extract", "schema": schema, "strict": False},
            }
        resp = await self._client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        json_payload: dict[str, Any] | list[Any] | None
        try:
            json_payload = json.loads(text)
        except json.JSONDecodeError:
            json_payload = None
        usage = resp.usage
        in_tok = getattr(usage, "prompt_tokens", 0) if usage else 0
        out_tok = getattr(usage, "completion_tokens", 0) if usage else 0
        return LLMResponse(
            text=text,
            json_payload=json_payload,
            provider=self.provider,
            model_id=self.model_id,
            input_tokens=in_tok,
            output_tokens=out_tok,
        )
