"""Scrapo — AI-native, agent-first web scraping with deterministic replay."""

from scrapo.api import crawl, extract, scrape
from scrapo.config import Config
from scrapo.results import ChunkView, CrawlResult, ExtractionView, ScrapeResult
from scrapo.types import (
    Budget,
    ChunkedDocument,
    ExtractionResult,
    FetchResult,
    ProvenanceTag,
    RunRecord,
    Tier,
)

__version__ = "0.5.0"

__all__ = [
    "Budget",
    "ChunkView",
    "ChunkedDocument",
    "Config",
    "CrawlResult",
    "ExtractionResult",
    "ExtractionView",
    "FetchResult",
    "ProvenanceTag",
    "RunRecord",
    "ScrapeResult",
    "Tier",
    "crawl",
    "extract",
    "scrape",
]
