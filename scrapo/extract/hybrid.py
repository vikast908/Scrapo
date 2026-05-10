"""Hybrid selector + LLM extractor with self-healing.

Pipeline:
  1. Look up cached selectors keyed by (host, schema_hash).
  2. Run the cheap selector path. If everything parses + validates, return.
  3. Otherwise call the LLM with the page markdown + JSON schema (subject to the budget).
  4. On LLM success, the LLM also surfaces CSS selectors per field. For array fields
     it returns a container selector plus per-subfield selectors. Verified selectors are
     persisted so the next run is free.
  5. A cached selector set that keeps failing validation is evicted.

Selector spec shape (internal, also how it is stored in the cache via `extra`):
  scalar field: {"selector": "h1.title", "type": "css"}
  list field:   {"selector": "ul.grid > li", "type": "list",
                 "extra": {"items": {"name": "h3", "price": ".price"}}}
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
from scrapo.extract.schema import list_fields, schema_hash, schema_to_jsonschema, schema_version
from scrapo.extract.selector_cache import SelectorCache
from scrapo.types import Budget, ExtractionResult, ProvenanceTag

log = structlog.get_logger(__name__)

# Drop a cached selector set after this many consecutive validation failures.
STALE_SELECTOR_THRESHOLD = 3
_LIST_KEYS = ("__list__", "__items__")


_PROMPT_TEMPLATE = """\
Extract structured fields from this page.

URL: {url}

PAGE CONTENT (markdown):
---
{content}
---

Return ONLY a single JSON object that matches the target schema.

Then, on a separate line *after* the JSON, return a second JSON object on a new line
prefixed by `SELECTORS:` whose keys are the field names you populated. For a scalar field
the value is a stable CSS selector string that locates it on the live page. For a field
that is an array of objects, the value is instead an object: a `__list__` key holding the
CSS selector for one repeating element, plus one key per subfield holding a CSS selector
relative to that repeating element. Example:

{{"name": "Foo", "price": 12, "tags": [{{"label": "a"}}, {{"label": "b"}}]}}
SELECTORS: {{"name": "h1.product-title", "price": ".price-tag", "tags": {{"__list__": "ul.tags > li", "label": "span"}}}}

If a field cannot be located, omit it from the SELECTORS map but still set its value in the
data JSON (use null for scalars, [] for arrays). Do NOT use markdown code fences."""


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
                log.info(
                    "scrapo.extract.cache_miss_validation", url=url, schema=sv, failures=failures
                )

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
        array_fields = sorted(list_fields(model))
        if array_fields:
            prompt += (
                "\n\nArray-of-object fields in this schema (use the __list__ form for these): "
                + ", ".join(array_fields)
            )
        resp = await self.llm.extract_json(prompt, schema=json_schema)

        data_json: dict[str, Any] | None = None
        raw_selectors: dict[str, Any] = {}

        if resp.json_payload and isinstance(resp.json_payload, dict):
            data_json = resp.json_payload
            raw_selectors = _scan_selectors_block(resp.text)
        elif resp.text:
            data_json, raw_selectors = _parse_data_and_selectors(resp.text)

        selectors = _normalize_selectors(raw_selectors)

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

        used = {k: v["selector"] for k, v in selectors.items()}
        return ExtractionResult(
            data=data,
            method="llm",
            selectors_used=used,
            schema_version=sv,
            model_pinned=f"{resp.provider}:{resp.model_id}",
            prompt_hash=PROMPT_HASH,
            llm_calls=1,
            cost_usd=resp.cost_usd,
            provenance=self._build_provenance(url, used),
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
            container = spec.get("selector")
            if not isinstance(container, str) or not container:
                continue
            if spec.get("type") == "list":
                items = _extract_list(tree, container, (spec.get("extra") or {}).get("items") or {})
                if items:
                    data[field_name] = items
                    used[field_name] = container
                continue
            node = _css_first(tree, container)
            if node is None:
                continue
            data[field_name] = node.text(strip=True)
            used[field_name] = container
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
            if spec.get("type") == "list":
                sub = (spec.get("extra") or {}).get("items") or {}
                try:
                    nodes = tree.css(spec["selector"])
                except Exception:  # noqa: S112 - bad LLM selector, just skip the field
                    continue
                if nodes and sub and any(_css_first(nodes[0], s) is not None for s in sub.values()):
                    verified[field_name] = spec
                continue
            node = _css_first(tree, spec["selector"])
            if node is None:
                continue
            target = data_json.get(field_name)
            if target is not None and _scalar_matches(target, node.text(strip=True)):
                verified[field_name] = spec
        return verified

    @staticmethod
    def _build_provenance(url: str, selectors: dict[str, str]) -> list[ProvenanceTag]:
        return [
            ProvenanceTag(url=url, selector_path=sel, byte_start=0, byte_end=0, heading_trail=[field])
            for field, sel in selectors.items()
        ]


def _css_first(node: Any, selector: str) -> Any:
    try:
        return node.css_first(selector)
    except Exception:
        return None


def _scalar_matches(target: Any, text: str) -> bool:
    """A cached scalar selector is trusted if its text matches what the LLM extracted."""
    if not isinstance(target, str):
        return True  # numbers / bools: the node existing is enough
    needle = target.strip().lower()
    haystack = text.lower()
    return bool(needle) and (needle in haystack or haystack in needle)


def _extract_list(tree: Any, container: str, sub: dict[str, str]) -> list[dict[str, str]]:
    try:
        nodes = tree.css(container)
    except Exception:
        return []
    items: list[dict[str, str]] = []
    for n in nodes:
        row: dict[str, str] = {}
        for subfield, subsel in sub.items():
            sn = _css_first(n, subsel)
            if sn is not None:
                row[subfield] = sn.text(strip=True)
        if row:
            items.append(row)
    return items


def _normalize_selectors(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Turn an LLM SELECTORS map (values: str or {__list__: ..., sub: ...}) into specs."""
    out: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if isinstance(value, str) and value.strip():
            out[key] = {"selector": value.strip(), "type": "css"}
        elif isinstance(value, dict):
            container = next((value[k] for k in _LIST_KEYS if isinstance(value.get(k), str)), None)
            if not isinstance(container, str) or not container.strip():
                continue
            sub = {
                sk: sv.strip()
                for sk, sv in value.items()
                if sk not in _LIST_KEYS and isinstance(sv, str) and sv.strip()
            }
            out[key] = {"selector": container.strip(), "type": "list", "extra": {"items": sub}}
    return out


def _parse_data_and_selectors(text: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Pull the leading JSON object and the SELECTORS: block from a free-form response."""
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
    return data, _selectors_from_text(parts[1]) if len(parts) > 1 else {}


def _scan_selectors_block(text: str) -> dict[str, Any]:
    if not text or "SELECTORS:" not in text:
        return {}
    return _selectors_from_text(text.split("SELECTORS:", 1)[1])


def _selectors_from_text(blob: str) -> dict[str, Any]:
    try:
        sel_obj = json.loads(blob.strip())
    except json.JSONDecodeError:
        return {}
    if not isinstance(sel_obj, dict):
        return {}
    return {k: v for k, v in sel_obj.items() if isinstance(v, (str, dict))}
