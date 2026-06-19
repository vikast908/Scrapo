"""Offline tests for the batch-scrape orchestrator.

All scrapes are faked (no network). Out-of-order completion is forced
deterministically with per-url asyncio.Events rather than real sleeps.
"""

from __future__ import annotations

import asyncio

import pytest

from scrapo.crawl.batch import BatchItem, batch_scrape, batch_scrape_stream
from scrapo.results import ScrapeResult


def _ok(url: str) -> ScrapeResult:
    return ScrapeResult(run_id="r-" + url, url=url, status=200)


async def test_empty_urls_returns_empty() -> None:
    async def scrape_fn(url: str) -> ScrapeResult:  # pragma: no cover - never called
        return _ok(url)

    assert await batch_scrape([], scrape_fn=scrape_fn) == []


async def test_results_in_input_order_despite_out_of_order_completion() -> None:
    urls = [f"u{i}" for i in range(5)]
    gates = {u: asyncio.Event() for u in urls}

    async def scrape_fn(url: str) -> ScrapeResult:
        await gates[url].wait()
        return _ok(url)

    async def run() -> list[BatchItem]:
        return await batch_scrape(urls, scrape_fn=scrape_fn, max_concurrency=8)

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    # Release in reverse completion order.
    for u in reversed(urls):
        gates[u].set()
        await asyncio.sleep(0)
    items = await task

    assert [it.url for it in items] == urls  # input order preserved
    assert all(it.error is None and it.result is not None for it in items)
    assert [it.result.url for it in items if it.result] == urls


async def test_concurrency_cap_respected() -> None:
    urls = [f"u{i}" for i in range(20)]
    cap = 4
    state = {"cur": 0, "peak": 0}
    release = asyncio.Event()

    async def scrape_fn(url: str) -> ScrapeResult:
        state["cur"] += 1
        state["peak"] = max(state["peak"], state["cur"])
        await release.wait()  # hold the slot so concurrency builds up to the cap
        state["cur"] -= 1
        return _ok(url)

    async def run() -> list[BatchItem]:
        return await batch_scrape(urls, scrape_fn=scrape_fn, max_concurrency=cap)

    task = asyncio.create_task(run())
    # Let all schedulable workers start; only `cap` should be in flight.
    for _ in range(10):
        await asyncio.sleep(0)
    assert state["peak"] <= cap
    assert state["peak"] == cap  # the cap should actually be reached
    release.set()
    items = await task
    assert len(items) == len(urls)


async def test_per_url_error_isolation() -> None:
    urls = ["good0", "boom", "good1"]

    async def scrape_fn(url: str) -> ScrapeResult:
        if url == "boom":
            raise RuntimeError("kaboom")
        return _ok(url)

    items = await batch_scrape(urls, scrape_fn=scrape_fn, max_concurrency=2)

    by_url = {it.url: it for it in items}
    assert by_url["boom"].result is None
    assert by_url["boom"].error is not None
    assert "kaboom" in by_url["boom"].error
    for u in ("good0", "good1"):
        assert by_url[u].error is None
        assert by_url[u].result is not None


async def test_on_result_fires_once_per_url() -> None:
    urls = [f"u{i}" for i in range(6)]
    seen: list[str] = []

    async def scrape_fn(url: str) -> ScrapeResult:
        if url == "u3":
            raise ValueError("nope")
        return _ok(url)

    async def on_result(item: BatchItem) -> None:
        seen.append(item.url)

    items = await batch_scrape(
        urls, scrape_fn=scrape_fn, max_concurrency=3, on_result=on_result
    )
    assert sorted(seen) == sorted(urls)  # once per url, failures included
    assert len(seen) == len(urls)
    assert len(items) == len(urls)


async def test_max_concurrency_coerced_to_at_least_one() -> None:
    urls = ["a", "b", "c"]

    async def scrape_fn(url: str) -> ScrapeResult:
        return _ok(url)

    items = await batch_scrape(urls, scrape_fn=scrape_fn, max_concurrency=0)
    assert [it.url for it in items] == urls


async def test_stream_yields_all_items() -> None:
    urls = [f"u{i}" for i in range(7)]

    async def scrape_fn(url: str) -> ScrapeResult:
        await asyncio.sleep(0)
        return _ok(url)

    out: list[BatchItem] = []
    async for item in batch_scrape_stream(urls, scrape_fn=scrape_fn, max_concurrency=3):
        out.append(item)

    assert {it.url for it in out} == set(urls)
    assert len(out) == len(urls)
    assert all(it.error is None for it in out)


async def test_stream_empty() -> None:
    async def scrape_fn(url: str) -> ScrapeResult:  # pragma: no cover - never called
        return _ok(url)

    out = [item async for item in batch_scrape_stream([], scrape_fn=scrape_fn)]
    assert out == []


async def test_stream_error_isolation() -> None:
    urls = ["ok0", "bad", "ok1"]

    async def scrape_fn(url: str) -> ScrapeResult:
        if url == "bad":
            raise RuntimeError("explode")
        return _ok(url)

    by_url: dict[str, BatchItem] = {}
    async for item in batch_scrape_stream(urls, scrape_fn=scrape_fn, max_concurrency=2):
        by_url[item.url] = item

    assert by_url["bad"].error is not None and by_url["bad"].result is None
    assert by_url["ok0"].result is not None
    assert by_url["ok1"].result is not None


async def test_stream_early_break_does_not_leak_producer() -> None:
    urls = [f"u{i}" for i in range(50)]
    started = 0
    cancelled = asyncio.Event()

    async def scrape_fn(url: str) -> ScrapeResult:
        nonlocal started
        started += 1
        try:
            await asyncio.sleep(0)
            return _ok(url)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    gen = batch_scrape_stream(urls, scrape_fn=scrape_fn, max_concurrency=4)
    count = 0
    async for _item in gen:
        count += 1
        if count == 2:
            break  # early exit must tear the producer down
    # Closing the generator triggers the finally → producer cancel/cleanup.
    await gen.aclose()

    # Give the event loop a tick to process the cancellation, then assert we did
    # NOT run all 50 urls (the producer was stopped) and no task is dangling.
    await asyncio.sleep(0)
    assert started < len(urls)
    tasks = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
    assert tasks == []


def test_batchitem_is_slots() -> None:
    item = BatchItem(url="x", result=None, error=None)
    assert not hasattr(item, "__dict__")  # slots=True → no per-instance dict
    with pytest.raises((AttributeError, TypeError)):
        item.unexpected = 1  # type: ignore[attr-defined]
