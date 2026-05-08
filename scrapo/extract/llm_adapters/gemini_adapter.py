"""Gemini adapter."""

from __future__ import annotations

import json
import os
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse


class GeminiAdapter:
    provider = "gemini"

    def __init__(self, model_id: str | None = None, api_key: str | None = None) -> None:
        try:
            from google import genai
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
        return LLMResponse(
            text=text,
            json_payload=json_payload,
            provider=self.provider,
            model_id=self.model_id,
        )
