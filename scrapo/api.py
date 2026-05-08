"""Public surface — scrape, extract, crawl.

Wires the access router, document shaper, hybrid extractor, replay store, and
policy gate into three small async functions.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

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
from scrapo.policy.pii import PiiClassifier
from scrapo.policy.robots import RobotsGate
from scrapo.replay.store import ReplayStore
from scrapo.shape.provenance import shape_document
from scrapo.types import Budget, ChunkedDocument, ExtractionResult, FetchResult, RunRecord, Tier

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
) -> dict[str, Any]:
    """Single-URL scrape.

    Returns a dict containing run_id, fetch metadata, markdown, chunks (with
    provenance), and (when schema is given) typed extraction.
    """
    cfg = config or get_config()
    record = RunRecord.new(url)
    audit = AuditLog(cfg.audit_log, enabled=cfg.audit_enabled)
    robots = RobotsGate(cfg.user_agent, enabled=cfg.respect_robots)
    replay = ReplayStore(cfg)

    if not await robots.can_fetch(url):
        record.error = "blocked-by-robots"
        record.finished_at = time.time()
        await audit.record("scrape.blocked", run_id=record.run_id, url=url, reason="robots")
        await replay.record(record, None, None)
        return {"run_id": record.run_id, "blocked": True, "reason": "robots"}

    router = TierRouter(cfg, proxy_adapter=proxy_adapter)
    fetch = await router.fetch(
        url,
        budget=budget,
        wait_for=wait_for,
        screenshot=screenshot,
        storage_state=storage_state,
        force_tier=force_tier,
    )
    record.tier_used = fetch.tier_used
    record.proxy_region = fetch.proxy_region
    record.fetch_status = fetch.status

    if geo_policy and not geo_policy.is_allowed(fetch.proxy_region):
        record.error = f"geo-policy-violation:{fetch.proxy_region}"
        record.finished_at = time.time()
        await audit.record(
            "scrape.geo_violation", run_id=record.run_id, url=url, region=fetch.proxy_region
        )
        await replay.record(record, fetch, None)
        return {"run_id": record.run_id, "blocked": True, "reason": record.error}

    document = shape_document(fetch.html, url)

    extraction: ExtractionResult | None = None
    if schema is not None:
        cache = SelectorCache(cfg.selector_cache_db)
        extractor = HybridExtractor(cache, llm=llm_adapter, pin=pin, strict_pin=strict_pin)
        extraction = await extractor.extract(
            url=url,
            html=fetch.html,
            markdown=document.markdown,
            model=schema,
        )
        record.extraction_method = extraction.method
        record.model_pinned = extraction.model_pinned
        record.schema_version = extraction.schema_version
        record.llm_calls = extraction.llm_calls

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

    record.finished_at = time.time()
    await replay.record(record, fetch, extraction)
    await audit.record(
        "scrape.done",
        run_id=record.run_id,
        url=url,
        tier=fetch.tier_used.label,
        status=fetch.status,
        method=extraction.method if extraction else "none",
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
    proxy_adapter: ProxyAdapter | None = None,
    llm_adapter: LLMAdapter | None = None,
    pin: PinnedModel | None = None,
    on_page: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Recursive crawl. Each page goes through the same pipeline as scrape()."""
    from scrapo.crawl.scheduler import CrawlScheduler

    cfg = config or get_config()

    async def _scrape(url: str, *, budget: Budget | None = None) -> dict[str, Any]:
        return await scrape(
            url,
            schema=schema,
            config=cfg,
            budget=budget,
            proxy_adapter=proxy_adapter,
            llm_adapter=llm_adapter,
            pin=pin,
        )

    scheduler = CrawlScheduler(cfg, scrape_fn=_scrape)
    stats = await scheduler.crawl(
        seeds,
        budget=budget,
        max_depth=max_depth,
        same_host_only=same_host_only,
        on_page=on_page,
    )
    return {"crawl_id": scheduler.crawl_id, "stats": stats}


def _build_result(
    record: RunRecord,
    fetch: FetchResult,
    document: ChunkedDocument,
    extraction: ExtractionResult | None,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "run_id": record.run_id,
        "url": fetch.final_url,
        "status": fetch.status,
        "tier_used": fetch.tier_used.label,
        "proxy_region": fetch.proxy_region,
        "blocked": fetch.blocked,
        "block_reason": fetch.block_reason,
        "elapsed_ms": fetch.elapsed_ms,
        "title": document.title,
        "markdown": document.markdown,
        "html": fetch.html,
        "chunks": [
            {"text": c.text, "provenance": c.provenance.to_dict()} for c in document.chunks
        ],
    }
    if extraction is not None:
        data = extraction.data
        if hasattr(data, "model_dump"):
            data = data.model_dump()
        out["extraction"] = {
            "data": data,
            "method": extraction.method,
            "selectors_used": extraction.selectors_used,
            "model_pinned": extraction.model_pinned,
            "schema_version": extraction.schema_version,
            "llm_calls": extraction.llm_calls,
        }
    return out
