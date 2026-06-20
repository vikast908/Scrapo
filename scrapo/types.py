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
    """Cost ceiling for a single scrape or crawl run.

    ``max_llm_calls`` / ``max_cost_usd`` are the caps; ``llm_calls_made`` /
    ``spent_usd`` are mutable running counters updated via :meth:`record_llm`
    as the run progresses.

    Concurrency note: in a crawl, ONE Budget instance is shared across
    concurrent asyncio workers. asyncio is single-threaded, so a check-then-call
    (``can_use_llm`` followed by an awaited LLM call) has a small over-budget
    race: N workers can all pass the check before any of them records its spend,
    so the run can exceed the cap by up to ~N calls. That overshoot is bounded
    by the concurrency level and is accepted by design — we deliberately do NOT
    add locks to this dataclass.
    """

    max_llm_calls: int | None = None
    max_cost_usd: float | None = None
    max_tier: Tier = Tier.AGENT
    max_pages: int | None = None
    # Mutable running totals across the whole scrape/crawl run.
    llm_calls_made: int = 0
    spent_usd: float = 0.0

    def can_use_llm(self, _calls_so_far: int = 0) -> bool:
        """Whether another LLM call is permitted under the configured caps.

        The positional ``_calls_so_far`` argument is retained only for
        backwards compatibility with old call sites; the decision is based on
        the internal counters, not the argument.
        """
        if self.max_llm_calls is not None and self.llm_calls_made >= self.max_llm_calls:
            return False
        return not (self.max_cost_usd is not None and self.spent_usd >= self.max_cost_usd)

    def record_llm(self, cost_usd: float) -> None:
        """Account for one LLM call that actually happened (and its cost)."""
        self.llm_calls_made += 1
        self.spent_usd += cost_usd

    def can_use_tier(self, tier: Tier) -> bool:
        return tier <= self.max_tier


@dataclass(slots=True)
class Conditional:
    """HTTP validators for a conditional GET (``If-None-Match`` / ``If-Modified-Since``)."""

    etag: str | None = None
    last_modified: str | None = None

    @property
    def is_empty(self) -> bool:
        return not (self.etag or self.last_modified)

    def headers(self) -> dict[str, str]:
        h: dict[str, str] = {}
        if self.etag:
            h["If-None-Match"] = self.etag
        if self.last_modified:
            h["If-Modified-Since"] = self.last_modified
        return h


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
    not_modified: bool = False  # server answered a conditional GET with 304 (content unchanged)

    @property
    def html_hash(self) -> str:
        return hashlib.sha256(self.html.encode("utf-8", errors="replace")).hexdigest()

    @property
    def content_type(self) -> str:
        return (self.headers.get("content-type") or self.headers.get("Content-Type") or "").split(";")[0].strip().lower()

    @property
    def etag(self) -> str | None:
        return self.headers.get("etag") or self.headers.get("ETag") or None

    @property
    def last_modified(self) -> str | None:
        return self.headers.get("last-modified") or self.headers.get("Last-Modified") or None

    def validators(self) -> Conditional:
        """The ETag / Last-Modified from the response, packaged for a later conditional GET."""
        return Conditional(etag=self.etag, last_modified=self.last_modified)


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


ExtractionMethod = Literal["selector", "llm", "hybrid", "metadata", "none"]


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
    etag: str | None = None  # response validators, for a later conditional GET
    last_modified: str | None = None
    not_modified: bool = False  # this run was served from a 304 (content unchanged)

    @staticmethod
    def new(url: str) -> RunRecord:
        return RunRecord(run_id=uuid.uuid4().hex, url=url, started_at=time.time())
