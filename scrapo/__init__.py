"""Scrapo — AI-native, agent-first web scraping with deterministic replay."""

from scrapo.api import crawl, extract, scrape
from scrapo.config import Config
from scrapo.types import (
    Budget,
    ChunkedDocument,
    ExtractionResult,
    FetchResult,
    ProvenanceTag,
    RunRecord,
    Tier,
)

__version__ = "0.1.0"

__all__ = [
    "Budget",
    "ChunkedDocument",
    "Config",
    "ExtractionResult",
    "FetchResult",
    "ProvenanceTag",
    "RunRecord",
    "Tier",
    "crawl",
    "extract",
    "scrape",
]
