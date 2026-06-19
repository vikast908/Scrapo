import asyncio

import pytest

from scrapo.crawl.dedup import UrlDeduper, normalize_url


def test_normalize_strips_fragment_and_tracking():
    assert (
        normalize_url("https://Example.com/path?utm_source=x&id=2#frag")
        == "https://example.com/path?id=2"
    )


def test_normalize_default_port():
    assert normalize_url("http://example.com:80/x") == "http://example.com/x"
    assert normalize_url("https://example.com:443/x") == "https://example.com/x"


def test_normalize_sorts_query():
    assert (
        normalize_url("https://example.com/?b=2&a=1")
        == "https://example.com/?a=1&b=2"
    )


@pytest.mark.asyncio
async def test_url_deduper():
    d = UrlDeduper()
    assert await d.add("https://example.com/x")
    assert not await d.add("https://EXAMPLE.com/x?utm_x=1")
    assert await d.add("https://example.com/y")
    assert "https://example.com/x" in d


@pytest.mark.asyncio
async def test_concurrent_add_same_url_enqueues_once():
    """Concurrent add() of the same URL must return True exactly once."""
    d = UrlDeduper()
    url = "https://example.com/race"
    results = await asyncio.gather(*(d.add(url) for _ in range(50)))
    assert sum(1 for r in results if r) == 1
