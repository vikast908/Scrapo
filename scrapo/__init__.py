"""Scrapo — AI-native, agent-first web scraping with deterministic replay."""

from scrapo.access.actions import Action
from scrapo.api import (
    batch_scrape,
    batch_scrape_stream,
    crawl,
    crawl_stream,
    extract,
    map_site,
    scrape,
)
from scrapo.config import Config
from scrapo.crawl.batch import BatchItem
from scrapo.results import ChunkView, CrawlResult, ExtractionView, ScrapeResult
from scrapo.sync import (
    batch_scrape_sync,
    crawl_sync,
    extract_sync,
    map_site_sync,
    scrape_sync,
)
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

__version__ = "0.9.0"

__all__ = [
    "Action",
    "BatchItem",
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
    "batch_scrape",
    "batch_scrape_stream",
    "batch_scrape_sync",
    "crawl",
    "crawl_stream",
    "crawl_sync",
    "extract",
    "extract_sync",
    "map_site",
    "map_site_sync",
    "scrape",
    "scrape_sync",
    "watch",
]
