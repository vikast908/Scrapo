"""Async batch-scrape orchestration.

Scrapes a *flat list* of URLs (not a recursive crawl) with bounded concurrency,
per-URL error isolation, and ordered or streamed results.

This module is pure orchestration: the caller injects ``scrape_fn`` so the shared
:class:`TierRouter`, selector cache, replay store, and audit log are reused across
the whole batch — exactly as :func:`scrapo.api.crawl` wires up its ``_scrape``
closure. It mirrors the semaphore + ``asyncio.TaskGroup`` concurrency pattern of
:class:`scrapo.crawl.scheduler.CrawlScheduler` and the bounded-queue back-pressure
of :func:`scrapo.api.crawl_stream`.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

import structlog

from scrapo.results import ScrapeResult

log = structlog.get_logger(__name__)

ScrapeFn = Callable[[str], Awaitable[ScrapeResult]]


@dataclass(slots=True)
class BatchItem:
    """One URL's outcome. Exactly one of ``result`` / ``error`` is set:
    ``result`` on success, ``error`` (the stringified exception) when the scrape
    raised. A *blocked* page is still a success — it comes back as a ScrapeResult
    with ``blocked=True`` — only an actual raised exception lands in ``error``."""

    url: str
    result: ScrapeResult | None
    error: str | None


async def batch_scrape(
    urls: list[str],
    *,
    scrape_fn: ScrapeFn,
    max_concurrency: int = 8,
    on_result: Callable[[BatchItem], Awaitable[None]] | None = None,
) -> list[BatchItem]:
    """Scrape every url in ``urls`` with at most ``max_concurrency`` in flight.

    Returns a :class:`BatchItem` per input url **in the same order as ``urls``**
    (completion order is irrelevant — slots are filled by index). A failure for
    one url is captured on its item and never aborts the rest. ``on_result``, if
    given, fires once per url as it completes (completion order).
    """
    if not urls:
        return []
    max_concurrency = max(1, max_concurrency)

    # Pre-size the output so each task writes its own slot by index; this is what
    # preserves input order independently of which url finishes first.
    items: list[BatchItem | None] = [None] * len(urls)
    sem = asyncio.Semaphore(max_concurrency)

    async def worker(index: int, url: str) -> None:
        async with sem:
            item = await _run_one(url, scrape_fn)
        items[index] = item
        if on_result is not None:
            # Isolate the consumer callback: a failing on_result must not abort
            # the batch (TaskGroup would cancel siblings on an escaping error).
            try:
                await on_result(item)
            except Exception as exc:  # noqa: BLE001 - logged, then swallowed on purpose
                log.warning("scrapo.batch.on_result_error", url=url, err=str(exc))

    async with asyncio.TaskGroup() as tg:
        for index, url in enumerate(urls):
            tg.create_task(worker(index, url))

    # Every slot is filled once the TaskGroup exits (all tasks ran to completion,
    # errors captured per-item), so the cast to a non-optional list is sound.
    return [item for item in items if item is not None]


async def batch_scrape_stream(
    urls: list[str],
    *,
    scrape_fn: ScrapeFn,
    max_concurrency: int = 8,
) -> AsyncIterator[BatchItem]:
    """Like :func:`batch_scrape`, but yields each :class:`BatchItem` as it
    completes (completion order, not input order).

    Memory is bounded two ways: a semaphore caps in-flight scrapes at
    ``max_concurrency``, and a bounded :class:`asyncio.Queue` (maxsize
    ``max_concurrency * 2``) back-pressures the producer so a slow consumer can't
    make the producer buffer every result. Breaking out of the ``async for``
    early cancels and cleans up the producer task.
    """
    if not urls:
        return
    max_concurrency = max(1, max_concurrency)

    queue: asyncio.Queue[object] = asyncio.Queue(maxsize=max(max_concurrency * 2, 2))
    sentinel: object = object()
    sem = asyncio.Semaphore(max_concurrency)

    async def worker(url: str) -> None:
        async with sem:
            item = await _run_one(url, scrape_fn)
        # put() blocks when the queue is full → back-pressure onto the scrapes.
        await queue.put(item)

    async def producer() -> None:
        try:
            async with asyncio.TaskGroup() as tg:
                for url in urls:
                    tg.create_task(worker(url))
        finally:
            await queue.put(sentinel)

    task = asyncio.create_task(producer())
    try:
        while True:
            item = await queue.get()
            if item is sentinel:
                break
            assert isinstance(item, BatchItem)
            yield item
        await task  # surface any unexpected error raised inside the producer
    finally:
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


async def _run_one(url: str, scrape_fn: ScrapeFn) -> BatchItem:
    """Run a single scrape with per-URL error isolation."""
    try:
        result = await scrape_fn(url)
    except Exception as exc:  # noqa: BLE001 - isolated per url; reported on the item
        log.warning("scrapo.batch.scrape_error", url=url, err=str(exc))
        return BatchItem(url=url, result=None, error=str(exc))
    return BatchItem(url=url, result=result, error=None)
