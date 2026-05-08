"""Deterministic mock adapter — used when no LLM credentials are available.

Echoes a JSON payload derived from heuristics so the rest of the pipeline can
be exercised in tests and demos without external API calls.
"""

from __future__ import annotations

import re
from typing import Any

from scrapo.extract.llm_adapters.base import LLMResponse


class MockAdapter:
    provider = "mock"
    model_id = "scrapo-mock-1"

    async def extract_json(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        max_tokens: int = 2048,
    ) -> LLMResponse:
        payload: dict[str, Any] = {}
        if schema and isinstance(schema, dict):
            for field_name, spec in (schema.get("properties") or {}).items():
                payload[field_name] = self._guess(prompt, field_name, spec)
        return LLMResponse(
            text="{}",
            json_payload=payload,
            provider=self.provider,
            model_id=self.model_id,
        )

    @staticmethod
    def _guess(prompt: str, name: str, spec: dict[str, Any]) -> Any:
        t = spec.get("type", "string")
        if t == "string":
            for line in prompt.splitlines():
                if name.lower() in line.lower():
                    cleaned = re.sub(r"[#*>`]+", "", line).strip()
                    return cleaned[:200] or None
            return None
        if t == "integer":
            m = re.search(rf"{re.escape(name)}\D+(\d+)", prompt, re.I)
            return int(m.group(1)) if m else None
        if t == "number":
            m = re.search(rf"{re.escape(name)}\D+([\d.]+)", prompt, re.I)
            return float(m.group(1)) if m else None
        if t == "boolean":
            return None
        if t == "array":
            return []
        return None
