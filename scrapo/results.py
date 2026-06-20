"""Typed return objects for the public API.

`scrape()`, `extract()`, and `crawl()` return these Pydantic models. They also
support `result["key"]`, `result.get("key", default)` and `"key" in result` so
code written against the 0.1 dict shape keeps working; new code should prefer
attribute access (`result.markdown`) and `result.model_dump()` for serialization.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class _Mappingish(BaseModel):
    """Adds dict-style read access on top of a Pydantic model (back-compat shim)."""

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:  # pragma: no cover - mirrors dict semantics
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __contains__(self, key: str) -> bool:
        # "in" reports *meaningful* membership: the field is defined and has a
        # non-None value. The 0.1 dict shape was sparse — fields the pipeline
        # didn't populate were absent — so callers used ``"markdown" not in result``
        # to detect "we didn't produce one". Returning True for ``key`` whose
        # value is None would silently flip that check.
        return getattr(self, key, None) is not None


class ChunkView(_Mappingish):
    text: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class ExtractionView(_Mappingish):
    data: Any = None
    method: str = "none"
    selectors_used: dict[str, Any] = Field(default_factory=dict)
    model_pinned: str | None = None
    schema_version: str | None = None
    llm_calls: int = 0
    cost_usd: float = 0.0


class ScrapeResult(_Mappingish):
    run_id: str
    url: str
    status: int | None = None
    tier_used: str | None = None
    via: str | None = None  # "api:<provider>" when served from a site's public API instead of the page
    proxy_region: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    not_modified: bool = False  # served from a 304 conditional GET — content is unchanged
    elapsed_ms: float | None = None
    kind: str = "html"  # "html" | "json" | "feed" | "pdf" | "text"
    title: str | None = None
    markdown: str | None = None
    html: str | None = None
    data: Any = None  # parsed structure for JSON / feed content
    chunks: list[ChunkView] = Field(default_factory=list)
    captured_json: list[dict[str, Any]] = Field(default_factory=list)  # XHR/fetch JSON seen in the browser tier
    extraction: ExtractionView | None = None
    cost_usd: float = 0.0


class CrawlResult(_Mappingish):
    crawl_id: str
    stats: dict[str, int] = Field(default_factory=dict)
