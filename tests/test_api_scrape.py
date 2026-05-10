"""End-to-end scrape() with the HTTP tier monkeypatched to a fixed page."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from scrapo.api import crawl, scrape
from scrapo.results import CrawlResult, ScrapeResult
from scrapo.types import Budget, FetchResult, Tier

PAGE = """\
<!doctype html><html><head><title>Demo</title></head>
<body><main>
  <h1>Hello</h1>
  <p>Welcome to the demo.</p>
</main></body></html>
"""


class FakeHttp:
    async def fetch(self, url, **kwargs):
        return FetchResult(
            url=url, final_url=url, status=200, html=PAGE,
            headers={"content-type": "text/html"}, tier_used=Tier.HTTP,
        )


class BlockedHttp:
    async def fetch(self, url, **kwargs):
        return FetchResult(
            url=url, final_url=url, status=403, html="", headers={}, tier_used=Tier.HTTP,
            blocked=True, block_reason="http-403",
        )


def _stub_router(monkeypatch, http_tier):
    from scrapo import api

    class StubRouter(api.TierRouter):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.http = http_tier
            self.browser = http_tier

    monkeypatch.setattr(api, "TierRouter", StubRouter)


@pytest.mark.asyncio
async def test_scrape_returns_markdown_and_run_id(isolated_config, monkeypatch):
    from scrapo import api

    real_router = api.TierRouter

    class StubRouter(real_router):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.http = FakeHttp()
            self.browser = FakeHttp()

    monkeypatch.setattr(api, "TierRouter", StubRouter)

    result = await scrape("https://example.com/", config=isolated_config)
    assert result["status"] == 200
    assert result["tier_used"] == "http"
    assert "Hello" in result["markdown"]
    assert result["chunks"]
    assert result["run_id"]


@pytest.mark.asyncio
async def test_scrape_robots_blocked_result_includes_url(isolated_config, monkeypatch):
    from scrapo import api

    class BlockedRobots:
        def __init__(self, *args, **kwargs):
            pass

        async def can_fetch(self, url):
            return False

    monkeypatch.setattr(api, "RobotsGate", BlockedRobots)
    isolated_config.respect_robots = True

    result = await scrape("https://www.linkedin.com/", config=isolated_config)

    assert result["blocked"] is True
    assert result["url"] == "https://www.linkedin.com/"
    assert result["block_reason"] == "robots"


class TinyDoc(BaseModel):
    title: str | None = None


@pytest.mark.asyncio
async def test_scrape_with_schema_records_extraction(isolated_config, monkeypatch):
    from scrapo import api

    real_router = api.TierRouter

    class StubRouter(real_router):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.http = FakeHttp()
            self.browser = FakeHttp()

    monkeypatch.setattr(api, "TierRouter", StubRouter)

    result = await scrape(
        "https://example.com/",
        schema=TinyDoc,
        config=isolated_config,
        budget=Budget(max_tier=Tier.HTTP),
    )
    assert "extraction" in result
    assert result["extraction"]["schema_version"].startswith("TinyDoc@")


@pytest.mark.asyncio
async def test_scrape_blocks_internal_url(isolated_config):
    result = await scrape("http://localhost:6379/", config=isolated_config)
    assert result["blocked"] is True
    assert result["block_reason"].startswith("ssrf-blocked:")
    assert result["status"] is None


@pytest.mark.asyncio
async def test_scrape_allows_internal_url_when_configured(isolated_config, monkeypatch):
    _stub_router(monkeypatch, FakeHttp())
    isolated_config.allow_private_hosts = True
    result = await scrape("http://localhost:8080/", config=isolated_config)
    assert not result["blocked"]
    assert "Hello" in result["markdown"]


@pytest.mark.asyncio
async def test_scrape_short_circuits_on_fetch_block(isolated_config, monkeypatch):
    _stub_router(monkeypatch, BlockedHttp())
    result = await scrape("https://example.com/", config=isolated_config)
    assert result["blocked"] is True
    assert result["block_reason"] == "http-403"
    assert result["status"] == 403
    assert "markdown" not in result


@pytest.mark.asyncio
async def test_scrape_returns_typed_result(isolated_config, monkeypatch):
    _stub_router(monkeypatch, FakeHttp())
    result = await scrape("https://example.com/", config=isolated_config)
    assert isinstance(result, ScrapeResult)
    assert result.markdown == result["markdown"]
    assert "markdown" in result
    assert "extraction" not in result  # None when no schema
    dumped = result.model_dump()
    assert dumped["run_id"] == result.run_id


@pytest.mark.asyncio
async def test_crawl_returns_typed_result(isolated_config, monkeypatch):
    _stub_router(monkeypatch, FakeHttp())
    result = await crawl(["https://example.com/"], config=isolated_config, max_depth=0)
    assert isinstance(result, CrawlResult)
    assert result.crawl_id
    assert sum(result.stats.values()) >= 1
    assert result["stats"] == result.stats
