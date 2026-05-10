"""Scrapo — AI-native, agent-first web scraping with deterministic replay."""

from scrapo.api import crawl, crawl_stream, extract, scrape
from scrapo.config import Config
from scrapo.results import ChunkView, CrawlResult, ExtractionView, ScrapeResult
from scrapo.types import (
    Budget,
    ChunkedDocument,
    Conditional,
    ExtractionResult,
    FetchResult,
    ProvenanceTag,
    RunRecord,
    Tier,
)
from scrapo.watch import ChangeSet, Watch, watch

__version__ = "0.7.0"

__all__ = [
    "Budget",
    "ChangeSet",
    "ChunkView",
    "ChunkedDocument",
    "Conditional",
    "Config",
    "CrawlResult",
    "ExtractionResult",
    "ExtractionView",
    "FetchResult",
    "ProvenanceTag",
    "RunRecord",
    "ScrapeResult",
    "Tier",
    "Watch",
    "crawl",
    "crawl_stream",
    "extract",
    "scrape",
    "watch",
]
