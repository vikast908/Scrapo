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
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit

import structlog
from selectolax.parser import HTMLParser

from scrapo.config import Config
from scrapo.crawl.dedup import UrlDeduper, normalize_url
from scrapo.crawl.queue import RequestQueue
from scrapo.crawl.sitemap import discover_sitemap_urls
from scrapo.policy.robots import RobotsGate
from scrapo.results import ScrapeResult
from scrapo.security import is_url_allowed
from scrapo.types import Budget

log = structlog.get_logger(__name__)


PageHandler = Callable[[ScrapeResult], Awaitable[None]]

_SKIP_LINK_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".zip", ".gz", ".tar", ".bz2", ".7z", ".rar",
    ".mp4", ".webm", ".mov", ".avi", ".mp3", ".wav", ".ogg", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


class CrawlScheduler:
    def __init__(
        self,
        config: Config,
        scrape_fn: Callable[..., Awaitable[ScrapeResult]],
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
        use_sitemap: bool = False,
        on_page: PageHandler | None = None,
    ) -> dict[str, int]:
        budget = budget or Budget(max_tier=self.config.default_max_tier, max_pages=200)
        seed_hosts = {_canonical_host(urlparse(s).netloc) for s in seeds}

        async def _enqueue(u: str, *, depth: int, parent: str | None) -> None:
            if same_host_only and _canonical_host(urlparse(u).netloc) not in seed_hosts:
                return
            n = normalize_url(u)
            if await self.dedup.add(n):
                await self.queue.enqueue(n, depth=depth, parent_url=parent)

        for seed in seeds:
            await _enqueue(seed, depth=0, parent=None)
        if use_sitemap:
            origins = {f"{p.scheme}://{p.netloc}" for p in (urlparse(s) for s in seeds) if p.netloc}
            for origin in origins:
                for u in await discover_sitemap_urls(origin, user_agent=self.config.user_agent):
                    if u.startswith(("http://", "https://")) and not urlsplit(u).path.lower().endswith(
                        _SKIP_LINK_EXTENSIONS
                    ) and is_url_allowed(u, allow_private=self.config.allow_private_hosts):
                        await _enqueue(u, depth=0, parent=None)

        sem = asyncio.Semaphore(self.config.max_concurrency)
        pages_done = 0
        in_flight = 0

        async def worker(req: dict[str, Any]) -> None:
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
            except Exception as e:
                # Genuine fetch/scrape failure — mark failed and (maybe) retry.
                log.warning("scrapo.crawl.fetch_error", url=url, err=str(e))
                await self.queue.fail(req["id"], str(e), retry=req["attempts"] < 2)
                return

            # The fetch succeeded: complete and count the page BEFORE running any
            # consumer callback so a callback failure can never trigger a re-fetch.
            await self.queue.complete(req["id"])
            pages_done += 1

            if on_page:
                # Isolate the consumer callback: a failing on_page (or a failing
                # internal emit in crawl_stream) must not retry an already-done page.
                try:
                    await on_page(result)
                except Exception as e:  # noqa: BLE001 - logged, then swallowed on purpose
                    log.warning("scrapo.crawl.on_page_error", url=url, err=str(e))

            # Link/pagination discovery happens after the page is already counted,
            # so an enqueue hiccup must not re-fail the page; log and move on.
            try:
                html = result.get("html") or ""
                # follow rel="next" pagination at the same depth (max_pages still bounds it).
                # Routed through _enqueue so it gets the same normalization + dedup as links.
                next_url = _next_link(url, html)
                if next_url:
                    await _enqueue(next_url, depth=depth, parent=url)
                if depth < max_depth:
                    for link in self._extract_links(url, html):
                        await _enqueue(link, depth=depth + 1, parent=url)
            except Exception as e:  # noqa: BLE001 - logged, then swallowed on purpose
                log.warning("scrapo.crawl.enqueue_error", url=url, err=str(e))

        async def bound(req: dict[str, Any]) -> None:
            nonlocal in_flight
            try:
                async with sem:
                    await worker(req)
            finally:
                in_flight -= 1

        async with asyncio.TaskGroup() as tg:
            while True:
                if budget.max_pages is not None and pages_done >= budget.max_pages:
                    break
                req = await self.queue.claim()
                if req is None:
                    if in_flight == 0:
                        break
                    await asyncio.sleep(0.05)
                    continue
                in_flight += 1
                tg.create_task(bound(req))

        return await self.queue.stats()

    async def _respect_host_delay(self, host: str, url: str) -> None:
        delay = await self.robots.crawl_delay(url) or 0.0
        # Reserve the slot under the lock, then sleep *outside* it so workers
        # for other hosts aren't blocked behind one host's crawl-delay.
        async with self._host_lock:
            now = time.monotonic()
            next_ok = self._host_next_ok.get(host, 0.0)
            my_slot = max(now, next_ok)
            self._host_next_ok[host] = my_slot + delay
        wait = my_slot - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)

    def _extract_links(self, base_url: str, html: str) -> list[str]:
        if not html:
            return []
        tree = HTMLParser(html)
        out: list[str] = []
        for a in tree.css("a[href]"):
            href = (a.attributes.get("href") or "").strip() if a.attributes else ""
            if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
                continue
            absu = urljoin(base_url, href)
            if not absu.startswith(("http://", "https://")):
                continue
            if urlsplit(absu).path.lower().endswith(_SKIP_LINK_EXTENSIONS):
                continue
            if not is_url_allowed(absu, allow_private=self.config.allow_private_hosts):
                continue
            out.append(normalize_url(absu))
        return out


def _canonical_host(netloc: str) -> str:
    """Lowercased host with a leading ``www.`` stripped, so ``example.com`` and
    ``www.example.com`` are treated as the same host for scope checks. Does NOT
    broaden to arbitrary subdomains."""
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _next_link(base_url: str, html: str) -> str | None:
    """Find a rel="next" pagination link (<link rel=next> or <a rel=next>)."""
    if not html or "next" not in html.lower():
        return None
    tree = HTMLParser(html)
    for sel in ('link[rel~="next"]', 'a[rel~="next"]'):
        node = tree.css_first(sel)
        if node is None or not node.attributes:
            continue
        href = (node.attributes.get("href") or "").strip()
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            continue
        absu = urljoin(base_url, href)
        if absu.startswith(("http://", "https://")):
            return absu
    return None
