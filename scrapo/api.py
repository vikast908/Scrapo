"""Public surface — scrape, extract, crawl.

Wires the access router, document shaper, hybrid extractor, replay store, and
policy gate into three small async functions.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog
from pydantic import BaseModel

from scrapo.access.adapters.base import ProxyAdapter
from scrapo.access.router import TierRouter
from scrapo.config import Config, get_config
from scrapo.extract.hybrid import HybridExtractor
from scrapo.extract.llm_adapters.base import LLMAdapter
from scrapo.extract.pinning import PinnedModel
from scrapo.extract.selector_cache import SelectorCache
from scrapo.policy.audit import AuditLog
from scrapo.policy.geo import GeoPolicy
from scrapo.policy.pii import PiiClassifier, redact
from scrapo.policy.robots import RobotsGate
from scrapo.replay.store import ReplayStore
from scrapo.results import ChunkView, CrawlResult, ExtractionView, ScrapeResult
from scrapo.security import SsrfError, check_url
from scrapo.shape.dispatch import shape_fetch
from scrapo.shape.provenance import shape_document
from scrapo.types import (
    Budget,
    ChunkedDocument,
    Conditional,
    ExtractionResult,
    FetchResult,
    RunRecord,
    Tier,
)

log = structlog.get_logger(__name__)


async def scrape(
    url: str,
    *,
    schema: type[BaseModel] | None = None,
    config: Config | None = None,
    budget: Budget | None = None,
    proxy_adapter: ProxyAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
    pin: PinnedModel | None = None,
    strict_pin: bool = False,
    geo_policy: GeoPolicy | None = None,
    wait_for: str | None = None,
    screenshot: bool = False,
    storage_state: str | None = None,
    force_tier: Tier | None = None,
    router: TierRouter | None = None,
) -> ScrapeResult:
    """Single-URL scrape.

    Returns a :class:`~scrapo.results.ScrapeResult` with run_id, fetch metadata,
    markdown, chunks (with provenance), and (when ``schema`` is given) typed
    extraction. Pass ``router`` to reuse one :class:`TierRouter` (and its browser
    pool) across many calls; otherwise a fresh one is created and torn down here.
    """
    cfg = config or get_config()
    record = RunRecord.new(url)
    audit = AuditLog(cfg.audit_log, enabled=cfg.audit_enabled)
    replay = ReplayStore(cfg)

    def _blocked(*, url_: str, status: int | None, tier: str | None, reason: str) -> ScrapeResult:
        return ScrapeResult(
            run_id=record.run_id,
            url=url_,
            status=status,
            tier_used=tier,
            blocked=True,
            block_reason=reason,
        )

    try:
        check_url(url, allow_private=cfg.allow_private_hosts)
    except SsrfError as exc:
        record.error = f"ssrf-blocked:{exc}"
        record.finished_at = time.time()
        await audit.record("scrape.blocked", run_id=record.run_id, url=url, reason="ssrf")
        await replay.record(record, None, None)
        return _blocked(url_=url, status=None, tier=None, reason=record.error)

    if cfg.respect_robots:
        robots = RobotsGate(cfg.user_agent)
        if not await robots.can_fetch(url):
            record.error = "blocked-by-robots"
            record.finished_at = time.time()
            await audit.record("scrape.blocked", run_id=record.run_id, url=url, reason="robots")
            await replay.record(record, None, None)
            return _blocked(url_=url, status=None, tier=None, reason="robots")

    # Conditional GET fast path: when we've fetched this URL before via the HTTP
    # tier and the server gave us a validator, ask "has it changed?" — a 304 lets
    # us reuse the archived body (and skip the LLM) instead of re-scraping.
    conditional: Conditional | None = None
    prior: dict[str, Any] | None = None
    if cfg.conditional_requests and force_tier is None and not screenshot:
        candidate = await replay.last_run(url)
        if (
            candidate
            and not candidate.get("error")
            and candidate.get("tier_used") in (0, 1)
            and candidate.get("html_path")
            and (candidate.get("etag") or candidate.get("last_modified"))
        ):
            prior = candidate
            conditional = Conditional(
                etag=candidate.get("etag"), last_modified=candidate.get("last_modified")
            )

    reused_html_path: str | None = None
    own_router = router is None
    router = router or TierRouter(cfg, proxy_adapter=proxy_adapter)
    try:
        fetch = await router.fetch(
            url,
            budget=budget,
            wait_for=wait_for,
            screenshot=screenshot,
            storage_state=storage_state,
            force_tier=force_tier,
            conditional=conditional,
        )
        if fetch.not_modified:
            prior_html = await replay.load_html(prior["run_id"]) if prior is not None else None
            if prior is not None and prior_html is not None:
                reused_html_path = prior["html_path"]
                hdrs = _prior_headers(prior)
                # let the 304's own headers (e.g. a refreshed ETag) win over the archived ones
                for k, v in fetch.headers.items():
                    if v:
                        hdrs[k.lower()] = v
                fetch = FetchResult(
                    url=url,
                    final_url=url,
                    status=200,
                    html=prior_html,
                    headers=hdrs,
                    tier_used=Tier(prior["tier_used"]),
                    not_modified=True,
                )
            else:  # archived body is gone — fetch it for real
                fetch = await router.fetch(
                    url,
                    budget=budget,
                    wait_for=wait_for,
                    screenshot=screenshot,
                    storage_state=storage_state,
                    force_tier=force_tier,
                )
    finally:
        if own_router:
            await router.aclose()
    record.tier_used = fetch.tier_used
    record.proxy_region = fetch.proxy_region
    record.fetch_status = fetch.status
    record.etag = fetch.etag
    record.last_modified = fetch.last_modified
    record.not_modified = fetch.not_modified

    if fetch.blocked:
        record.error = fetch.block_reason or "blocked"
        record.finished_at = time.time()
        await audit.record(
            "scrape.blocked", run_id=record.run_id, url=url, reason=fetch.block_reason
        )
        await replay.record(record, fetch, None)
        return _blocked(
            url_=fetch.final_url, status=fetch.status, tier=fetch.tier_used.label, reason=record.error
        )

    if geo_policy and not geo_policy.is_allowed(fetch.proxy_region):
        record.error = f"geo-policy-violation:{fetch.proxy_region}"
        record.finished_at = time.time()
        await audit.record(
            "scrape.geo_violation", run_id=record.run_id, url=url, region=fetch.proxy_region
        )
        await replay.record(record, fetch, None)
        return _blocked(
            url_=fetch.final_url, status=fetch.status, tier=fetch.tier_used.label, reason=record.error
        )

    document = shape_fetch(fetch, url)

    extraction: ExtractionResult | None = None
    if schema is not None:
        cache = SelectorCache(cfg.selector_cache_db)
        extractor = HybridExtractor(cache, llm=llm_adapter, pin=pin, strict_pin=strict_pin)
        extraction = await extractor.extract(
            url=url,
            html=fetch.html,
            markdown=document.markdown,
            model=schema,
            budget=budget,
        )
        record.extraction_method = extraction.method
        record.model_pinned = extraction.model_pinned
        record.schema_version = extraction.schema_version
        record.llm_calls = extraction.llm_calls
        record.cost_usd = extraction.cost_usd

    if cfg.enable_pii_filter:
        pii = PiiClassifier()
        hits = pii.scan(document.markdown)
        if hits:
            await audit.record(
                "scrape.pii_detected",
                run_id=record.run_id,
                url=url,
                kinds=sorted({h.kind for h in hits}),
                count=len(hits),
            )

    if cfg.redact_snapshots:
        fetch.html = redact(fetch.html)
        document.markdown = redact(document.markdown)
        for chunk in document.chunks:
            chunk.text = redact(chunk.text)

    record.finished_at = time.time()
    await replay.record(record, fetch, extraction, html_path=reused_html_path)
    await audit.record(
        "scrape.done",
        run_id=record.run_id,
        url=url,
        tier=fetch.tier_used.label,
        status=fetch.status,
        method=extraction.method if extraction else "none",
        not_modified=fetch.not_modified,
    )

    return _build_result(record, fetch, document, extraction)


async def extract(
    *,
    html: str,
    url: str,
    schema: type[BaseModel],
    config: Config | None = None,
    llm_adapter: LLMAdapter | None = None,
    pin: PinnedModel | None = None,
    strict_pin: bool = False,
) -> ExtractionResult:
    """Run hybrid extraction over already-fetched HTML."""
    cfg = config or get_config()
    document = shape_document(html, url)
    cache = SelectorCache(cfg.selector_cache_db)
    extractor = HybridExtractor(cache, llm=llm_adapter, pin=pin, strict_pin=strict_pin)
    return await extractor.extract(
        url=url, html=html, markdown=document.markdown, model=schema
    )


async def crawl(
    seeds: list[str],
    *,
    schema: type[BaseModel] | None = None,
    config: Config | None = None,
    budget: Budget | None = None,
    max_depth: int = 2,
    same_host_only: bool = True,
    use_sitemap: bool = False,
    proxy_adapter: ProxyAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
    pin: PinnedModel | None = None,
    on_page: Callable[[ScrapeResult], Awaitable[None]] | None = None,
) -> CrawlResult:
    """Recursive crawl. Each page goes through the same pipeline as scrape().

    Follows ``rel="next"`` pagination automatically. With ``use_sitemap=True`` it
    also seeds from each origin's ``sitemap.xml`` (and any sitemap index it points to).
    """
    from scrapo.crawl.scheduler import CrawlScheduler

    cfg = config or get_config()
    shared_router = TierRouter(cfg, proxy_adapter=proxy_adapter)

    async def _scrape(url: str, *, budget: Budget | None = None) -> ScrapeResult:
        return await scrape(
            url,
            schema=schema,
            config=cfg,
            budget=budget,
            llm_adapter=llm_adapter,
            pin=pin,
            router=shared_router,
        )

    scheduler = CrawlScheduler(cfg, scrape_fn=_scrape)
    try:
        stats = await scheduler.crawl(
            seeds,
            budget=budget,
            max_depth=max_depth,
            same_host_only=same_host_only,
            use_sitemap=use_sitemap,
            on_page=on_page,
        )
    finally:
        await shared_router.aclose()
    return CrawlResult(crawl_id=scheduler.crawl_id, stats=stats)


async def crawl_stream(
    seeds: list[str],
    *,
    schema: type[BaseModel] | None = None,
    config: Config | None = None,
    budget: Budget | None = None,
    max_depth: int = 2,
    same_host_only: bool = True,
    use_sitemap: bool = False,
    proxy_adapter: ProxyAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
    pin: PinnedModel | None = None,
) -> AsyncIterator[ScrapeResult]:
    """Like :func:`crawl`, but yields each :class:`ScrapeResult` as it completes.

    Lets you process (or persist) pages incrementally instead of waiting for the
    whole crawl. Breaking out of the ``async for`` early stops the crawl and
    tears the shared browser down.
    """
    from scrapo.crawl.scheduler import CrawlScheduler

    cfg = config or get_config()
    shared_router = TierRouter(cfg, proxy_adapter=proxy_adapter)
    queue: asyncio.Queue[object] = asyncio.Queue()
    sentinel: object = object()

    async def _scrape(url: str, *, budget: Budget | None = None) -> ScrapeResult:
        return await scrape(
            url, schema=schema, config=cfg, budget=budget,
            llm_adapter=llm_adapter, pin=pin, router=shared_router,
        )

    async def _emit(result: ScrapeResult) -> None:
        await queue.put(result)

    scheduler = CrawlScheduler(cfg, scrape_fn=_scrape)

    async def _run() -> None:
        try:
            await scheduler.crawl(
                seeds, budget=budget, max_depth=max_depth,
                same_host_only=same_host_only, use_sitemap=use_sitemap, on_page=_emit,
            )
        finally:
            await queue.put(sentinel)

    task = asyncio.create_task(_run())
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            assert isinstance(item, ScrapeResult)
            yield item
        await task  # surface any exception raised inside the crawl
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        await shared_router.aclose()


def _prior_headers(prior: dict[str, Any]) -> dict[str, str]:
    """Response headers recorded on a prior run (so a 304 reconstruction keeps the
    content-type, etc.); lowercased, best-effort."""
    raw = prior.get("headers_json")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in parsed.items()}


def _build_result(
    record: RunRecord,
    fetch: FetchResult,
    document: ChunkedDocument,
    extraction: ExtractionResult | None,
) -> ScrapeResult:
    extraction_view: ExtractionView | None = None
    if extraction is not None:
        data = extraction.data
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        extraction_view = ExtractionView(
            data=data,
            method=extraction.method,
            selectors_used=extraction.selectors_used,
            model_pinned=extraction.model_pinned,
            schema_version=extraction.schema_version,
            llm_calls=extraction.llm_calls,
            cost_usd=extraction.cost_usd,
        )
    return ScrapeResult(
        run_id=record.run_id,
        url=fetch.final_url,
        status=fetch.status,
        tier_used=fetch.tier_used.label,
        proxy_region=fetch.proxy_region,
        blocked=fetch.blocked,
        block_reason=fetch.block_reason,
        not_modified=fetch.not_modified,
        elapsed_ms=fetch.elapsed_ms,
        kind=document.kind,
        title=document.title,
        markdown=document.markdown,
        html=fetch.html,
        data=document.data,
        chunks=[
            ChunkView(text=c.text, provenance=c.provenance.to_dict()) for c in document.chunks
        ],
        captured_json=list(fetch.captured_json),
        extraction=extraction_view,
        cost_usd=record.cost_usd,
    )
