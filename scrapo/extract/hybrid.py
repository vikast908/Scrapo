"""Hybrid selector + LLM extractor with self-healing.

Pipeline:
  1. Look up cached selectors keyed by (domain, schema_hash).
  2. Run cheap selector path. If all required fields parse + validate → return.
  3. Otherwise call LLM with the page markdown + JSON schema.
  4. On LLM success, ask the LLM to also surface CSS selectors for each field;
     persist them in the cache so the next run is free.
  5. Return ExtractionResult with method, selectors_used, model_pinned, etc.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError
from selectolax.parser import HTMLParser

from scrapo.extract.llm_adapters.base import LLMAdapter, get_default
from scrapo.extract.pinning import PinnedModel, matches, require_pin
from scrapo.extract.schema import schema_hash, schema_to_jsonschema, schema_version
from scrapo.extract.selector_cache import SelectorCache
from scrapo.types import Budget, ExtractionResult, ProvenanceTag

log = structlog.get_logger(__name__)

# Drop a cached selector set after this many consecutive validation failures.
STALE_SELECTOR_THRESHOLD = 3


_PROMPT_TEMPLATE = """\
Extract structured fields from this page.

URL: {url}

PAGE CONTENT (markdown):
---
{content}
---

Return ONLY a single JSON object that matches the target schema.

Then, on a separate line *after* the JSON, return a second JSON object on a new line
prefixed by `SELECTORS:` whose keys are the field names you populated and whose values
are stable CSS selectors that locate each field on the live page. Example:

{{"name": "Foo", "price": 12}}
SELECTORS: {{"name": "h1.product-title", "price": ".price-tag"}}

If a field cannot be located, omit it from the SELECTORS map but still set its value
to null in the data JSON. Do NOT use markdown code fences."""


PROMPT_HASH = hashlib.sha256(_PROMPT_TEMPLATE.encode("utf-8")).hexdigest()


class HybridExtractor:
    def __init__(
        self,
        cache: SelectorCache,
        llm: LLMAdapter | None = None,
        pin: PinnedModel | None = None,
        strict_pin: bool = False,
    ) -> None:
        self.cache = cache
        self.llm = llm or get_default()
        self.pin = pin
        self.strict_pin = strict_pin

    async def extract(
        self,
        *,
        url: str,
        html: str,
        markdown: str,
        model: type[BaseModel],
        budget: Budget | None = None,
    ) -> ExtractionResult:
        require_pin(self.pin, strict=self.strict_pin)
        sh = schema_hash(model)
        sv = schema_version(model)

        cached = await self.cache.get(url, sh)
        if cached:
            data, used = self._apply_selectors(html, model, cached)
            if data is not None:
                await self.cache.record_success(url, sh)
                provenance = self._build_provenance(url, used)
                return ExtractionResult(
                    data=data,
                    method="selector",
                    selectors_used=used,
                    schema_version=sv,
                    provenance=provenance,
                )
            failures = await self.cache.record_failure(url, sh)
            if failures >= STALE_SELECTOR_THRESHOLD:
                await self.cache.invalidate(url, sh)
                log.info("scrapo.extract.cache_evicted", url=url, schema=sv, failures=failures)
            else:
                log.info("scrapo.extract.cache_miss_validation", url=url, schema=sv, failures=failures)

        if budget is not None and not budget.can_use_llm(0):
            log.info("scrapo.extract.llm_budget_exhausted", url=url, schema=sv)
            return ExtractionResult(data=None, method="none", schema_version=sv)

        return await self._llm_extract(url, html, markdown, model, sh, sv)

    async def _llm_extract(
        self,
        url: str,
        html: str,
        markdown: str,
        model: type[BaseModel],
        sh: str,
        sv: str,
    ) -> ExtractionResult:
        if self.pin and not matches(self.pin, self.llm.provider, self.llm.model_id, PROMPT_HASH):
            from scrapo.extract.pinning import PinViolation

            raise PinViolation(
                f"adapter {self.llm.provider}:{self.llm.model_id} does not match pin {self.pin.identifier}"
            )

        json_schema = schema_to_jsonschema(model)
        prompt = _PROMPT_TEMPLATE.format(url=url, content=markdown[:24_000])
        resp = await self.llm.extract_json(prompt, schema=json_schema)

        data_json: dict[str, Any] | None = None
        selectors: dict[str, dict[str, Any]] = {}

        if resp.json_payload and isinstance(resp.json_payload, dict):
            data_json = resp.json_payload

        if data_json is None and resp.text:
            data_json, sel_map = _parse_data_and_selectors(resp.text)
            for k, v in (sel_map or {}).items():
                if isinstance(v, str) and v.strip():
                    selectors[k] = {"selector": v.strip(), "type": "css"}
        else:
            sel_map = _scan_selectors_block(resp.text)
            for k, v in sel_map.items():
                selectors[k] = {"selector": v, "type": "css"}

        if data_json is None:
            return ExtractionResult(
                data=None,
                method="none",
                schema_version=sv,
                model_pinned=f"{resp.provider}:{resp.model_id}",
                prompt_hash=PROMPT_HASH,
                llm_calls=1,
                cost_usd=resp.cost_usd,
            )

        try:
            data = model.model_validate(data_json)
        except ValidationError as e:
            log.warning("scrapo.extract.validation_failed", err=str(e), url=url)
            return ExtractionResult(
                data=data_json,
                method="llm",
                schema_version=sv,
                model_pinned=f"{resp.provider}:{resp.model_id}",
                prompt_hash=PROMPT_HASH,
                llm_calls=1,
                cost_usd=resp.cost_usd,
            )

        if selectors:
            verified = self._verify_selectors(html, selectors, data_json)
            if verified:
                await self.cache.put(url, sh, verified)

        provenance = self._build_provenance(url, {k: v["selector"] for k, v in selectors.items()})
        return ExtractionResult(
            data=data,
            method="llm",
            selectors_used={k: v["selector"] for k, v in selectors.items()},
            schema_version=sv,
            model_pinned=f"{resp.provider}:{resp.model_id}",
            prompt_hash=PROMPT_HASH,
            llm_calls=1,
            cost_usd=resp.cost_usd,
            provenance=provenance,
        )

    @staticmethod
    def _apply_selectors(
        html: str,
        model: type[BaseModel],
        cached: dict[str, dict[str, Any]],
    ) -> tuple[BaseModel | None, dict[str, str]]:
        tree = HTMLParser(html)
        data: dict[str, Any] = {}
        used: dict[str, str] = {}
        for field_name, spec in cached.items():
            sel = spec["selector"]
            node = tree.css_first(sel)
            if node is None:
                continue
            data[field_name] = node.text(strip=True)
            used[field_name] = sel
        try:
            return model.model_validate(data), used
        except ValidationError:
            return None, used

    @staticmethod
    def _verify_selectors(
        html: str,
        selectors: dict[str, dict[str, Any]],
        data_json: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        tree = HTMLParser(html)
        verified: dict[str, dict[str, Any]] = {}
        for field_name, spec in selectors.items():
            try:
                node = tree.css_first(spec["selector"])
            except Exception:  # noqa: S112 - a malformed LLM selector just gets skipped
                continue
            if node is None:
                continue
            text = node.text(strip=True)
            target = data_json.get(field_name)
            if target is None:
                continue
            if isinstance(target, str) and target.strip() and (
                target.strip().lower() in text.lower() or text.lower() in target.strip().lower()
            ) or not isinstance(target, str):
                verified[field_name] = spec
        return verified

    @staticmethod
    def _build_provenance(url: str, selectors: dict[str, str]) -> list[ProvenanceTag]:
        return [
            ProvenanceTag(url=url, selector_path=sel, byte_start=0, byte_end=0, heading_trail=[field])
            for field, sel in selectors.items()
        ]


def _parse_data_and_selectors(
    text: str,
) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Pull the leading JSON object and the SELECTORS: line from a free-form response."""
    if not text:
        return None, {}
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[-1]
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        if cleaned.endswith("```"):
            cleaned = cleaned[: cleaned.rfind("```")]
    parts = cleaned.split("SELECTORS:", 1)
    data: dict[str, Any] | None = None
    try:
        data = json.loads(parts[0].strip())
    except json.JSONDecodeError:
        end = parts[0].rfind("}")
        if end > 0:
            try:
                data = json.loads(parts[0][: end + 1])
            except json.JSONDecodeError:
                data = None
    sel: dict[str, str] = {}
    if len(parts) > 1:
        try:
            sel_obj = json.loads(parts[1].strip())
            if isinstance(sel_obj, dict):
                sel = {k: v for k, v in sel_obj.items() if isinstance(v, str)}
        except json.JSONDecodeError:
            pass
    return data, sel


def _scan_selectors_block(text: str) -> dict[str, str]:
    if not text or "SELECTORS:" not in text:
        return {}
    tail = text.split("SELECTORS:", 1)[1].strip()
    try:
        sel_obj = json.loads(tail)
    except json.JSONDecodeError:
        return {}
    if not isinstance(sel_obj, dict):
        return {}
    return {k: v for k, v in sel_obj.items() if isinstance(v, str)}
