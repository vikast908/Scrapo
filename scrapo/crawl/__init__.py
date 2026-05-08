"""Crawl primitives — persistent request queue, async scheduler, dedup."""

from scrapo.crawl.dedup import UrlDeduper
from scrapo.crawl.queue import RequestQueue
from scrapo.crawl.scheduler import CrawlScheduler

__all__ = ["CrawlScheduler", "RequestQueue", "UrlDeduper"]
