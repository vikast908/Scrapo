"""Async crawl scheduler.

Honors:
  - global concurrency cap (config.max_concurrency)
  - per-host crawl-delay (from robots.txt)
  - max_depth + same-host scope
  - budget (max_pages)

Uses the public scrape() pipeline so every crawled page automatically gets
the same access router, replay snapshot, and policy gate as a one-shot scrape.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import structlog
from selectolax.parser import HTMLParser

from scrapo.config import Config
from scrapo.crawl.dedup import UrlDeduper, normalize_url
from scrapo.crawl.queue import RequestQueue
from scrapo.policy.robots import RobotsGate
from scrapo.types import Budget

log = structlog.get_logger(__name__)


PageHandler = Callable[[dict], Awaitable[None]]


class CrawlScheduler:
    def __init__(
        self,
        config: Config,
        scrape_fn: Callable[..., Awaitable[dict]],
        robots: RobotsGate | None = None,
        crawl_id: str | None = None,
    ) -> None:
        self.config = config
        self.scrape_fn = scrape_fn
        self.robots = robots or RobotsGate(config.user_agent, enabled=config.respect_robots)
        self.crawl_id = crawl_id or uuid.uuid4().hex[:12]
        self.queue = RequestQueue(config.crawl_queue_db, self.crawl_id)
        self.dedup = UrlDeduper()
        self._host_next_ok: dict[str, float] = {}
        self._host_lock = asyncio.Lock()

    async def crawl(
        self,
        seeds: list[str],
        *,
        budget: Budget | None = None,
        max_depth: int = 2,
        same_host_only: bool = True,
        on_page: PageHandler | None = None,
    ) -> dict[str, int]:
        budget = budget or Budget(max_tier=self.config.default_max_tier, max_pages=200)
        for seed in seeds:
            n = normalize_url(seed)
            if self.dedup.add(n):
                await self.queue.enqueue(n, depth=0)

        seed_hosts = {urlparse(s).netloc for s in seeds}
        sem = asyncio.Semaphore(self.config.max_concurrency)
        pages_done = 0

        async def worker(req: dict) -> None:
            nonlocal pages_done
            url = req["url"]
            depth = req["depth"]
            host = urlparse(url).netloc

            if not await self.robots.can_fetch(url):
                log.info("scrapo.crawl.blocked_by_robots", url=url)
                await self.queue.complete(req["id"])
                return

            await self._respect_host_delay(host, url)

            try:
                result = await self.scrape_fn(url, budget=budget)
                await self.queue.complete(req["id"])
                pages_done += 1
                if on_page:
                    await on_page(result)
                if depth < max_depth:
                    for link in self._extract_links(url, result.get("html", "")):
                        if same_host_only and urlparse(link).netloc not in seed_hosts:
                            continue
                        if not self.dedup.add(link):
                            continue
                        await self.queue.enqueue(link, depth=depth + 1, parent_url=url)
            except Exception as e:
                log.warning("scrapo.crawl.fetch_error", url=url, err=str(e))
                await self.queue.fail(req["id"], str(e), retry=req["attempts"] < 2)

        async with asyncio.TaskGroup() as tg:
            while True:
                if budget.max_pages is not None and pages_done >= budget.max_pages:
                    break
                req = await self.queue.claim()
                if req is None:
                    if not _has_in_flight(tg):
                        break
                    await asyncio.sleep(0.1)
                    continue

                async def _bound(req=req):
                    async with sem:
                        await worker(req)

                tg.create_task(_bound())

        return await self.queue.stats()

    async def _respect_host_delay(self, host: str, url: str) -> None:
        delay = await self.robots.crawl_delay(url) or 0.0
        async with self._host_lock:
            now = time.monotonic()
            next_ok = self._host_next_ok.get(host, 0.0)
            if now < next_ok:
                await asyncio.sleep(next_ok - now)
            self._host_next_ok[host] = max(now, next_ok) + delay

    @staticmethod
    def _extract_links(base_url: str, html: str) -> list[str]:
        if not html:
            return []
        tree = HTMLParser(html)
        out: list[str] = []
        for a in tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip() if a.attributes else ""
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#")):
                continue
            absu = urljoin(base_url, href)
            out.append(normalize_url(absu))
        return out


def _has_in_flight(tg: asyncio.TaskGroup) -> bool:
    return any(not t.done() for t in getattr(tg, "_tasks", set()))
