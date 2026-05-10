"""Shared types and dataclasses used across all scrapo modules."""

from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal


class Tier(IntEnum):
    """Access tier — escalate from cheapest to most expensive."""

    HTTP = 0
    HTTP_SESSIONED = 1
    BROWSER = 2
    BROWSER_STEALTH = 3
    AGENT = 4

    @property
    def label(self) -> str:
        return {
            Tier.HTTP: "http",
            Tier.HTTP_SESSIONED: "http+session",
            Tier.BROWSER: "browser",
            Tier.BROWSER_STEALTH: "browser+stealth",
            Tier.AGENT: "agent",
        }[self]


@dataclass(slots=True)
class Budget:
    """Cost ceiling for a single scrape or crawl run."""

    max_llm_calls: int | None = None
    max_cost_usd: float | None = None
    max_tier: Tier = Tier.AGENT
    max_pages: int | None = None

    def can_use_llm(self, calls_so_far: int) -> bool:
        if self.max_llm_calls is None:
            return True
        return calls_so_far < self.max_llm_calls

    def can_use_tier(self, tier: Tier) -> bool:
        return tier <= self.max_tier


@dataclass(slots=True)
class FetchResult:
    """Result of a single page fetch — independent of extraction."""

    url: str
    final_url: str
    status: int
    html: str
    headers: dict[str, str]
    tier_used: Tier
    fetched_at: float = field(default_factory=time.time)
    elapsed_ms: float = 0.0
    proxy_region: str | None = None
    screenshot_png: bytes | None = None
    raw_content: bytes | None = None
    captured_json: list[dict[str, Any]] = field(default_factory=list)
    blocked: bool = False
    block_reason: str | None = None

    @property
    def html_hash(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8", errors="replace")).hexdigest()

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or self.headers.get("Content-Type") or "").split(";")[0].strip().lower()


@dataclass(slots=True)
class ProvenanceTag:
    """Where a chunk or field came from in the source document."""

    url: str
    selector_path: str
    byte_start: int
    byte_end: int
    heading_trail: list[str] = field(default_factory=list)
    chunk_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "selector_path": self.selector_path,
            "byte_start": self.byte_start,
            "byte_end": self.byte_end,
            "heading_trail": self.heading_trail,
            "chunk_hash": self.chunk_hash,
        }


@dataclass(slots=True)
class Chunk:
    text: str
    provenance: ProvenanceTag

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", errors="replace")).hexdigest()


@dataclass(slots=True)
class ChunkedDocument:
    """Markdown + chunks with per-chunk provenance.

    ``kind`` is ``"html"`` for ordinary pages, or ``"json"`` / ``"feed"`` /
    ``"pdf"`` / ``"text"`` when content-type routing produced the document a
    different way. ``data`` carries the parsed structure for JSON and feeds.
    """

    url: str
    title: str | None
    markdown: str
    chunks: list[Chunk]
    kind: str = "html"
    data: Any = None

    @property
    def total_tokens_est(self) -> int:
        return sum(len(c.text) for c in self.chunks) // 4


ExtractionMethod = Literal["selector", "llm", "hybrid", "none"]


@dataclass(slots=True)
class ExtractionResult:
    """Typed extraction output with the metadata needed for replay and pinning."""

    data: Any
    method: ExtractionMethod
    selectors_used: dict[str, str] = field(default_factory=dict)
    model_pinned: str | None = None
    prompt_hash: str | None = None
    schema_version: str | None = None
    llm_calls: int = 0
    cost_usd: float = 0.0
    provenance: list[ProvenanceTag] = field(default_factory=list)


@dataclass(slots=True)
class RunRecord:
    """Single scrape attempt — persisted for replay."""

    run_id: str
    url: str
    started_at: float
    finished_at: float | None = None
    tier_used: Tier = Tier.HTTP
    proxy_region: str | None = None
    fetch_status: int | None = None
    extraction_method: ExtractionMethod = "none"
    model_pinned: str | None = None
    schema_version: str | None = None
    cost_usd: float = 0.0
    llm_calls: int = 0
    error: str | None = None

    @staticmethod
    def new(url: str) -> RunRecord:
        return RunRecord(run_id=uuid.uuid4().hex, url=url, started_at=time.time())
