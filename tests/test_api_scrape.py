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


class RecordingHttp:
    """Returns a fixed page and records every URL it was asked to fetch.

    Defaults to a substantial (non-"thin") body so the tier router does not
    escalate and re-fetch — keeping the recorded fetch count meaningful.
    """

    def __init__(self, html=None):
        self.html = html if html is not None else _BIG_PAGE
        self.fetched: list[str] = []

    async def fetch(self, url, **kwargs):
        self.fetched.append(url)
        return FetchResult(
            url=url, final_url=url, status=200, html=self.html,
            headers={"content-type": "text/html"}, tier_used=Tier.HTTP,
        )


_BIG_PAGE = "<!doctype html><html><head><title>Demo</title></head><body><main><h1>Hello</h1><p>" + ("x " * 200) + "</p></main></body></html>"


class ConditionalHttp:
    """Returns the page with an ETag; answers a conditional GET with 304."""

    def __init__(self, etag='"v1"'):
        self.etag = etag
        self.conditionals_seen: list[object] = []

    async def fetch(self, url, *, tier=Tier.HTTP, conditional=None, **kwargs):
        self.conditionals_seen.append(conditional)
        if conditional is not None and not conditional.is_empty:
            return FetchResult(
                url=url, final_url=url, status=304, html="", headers={"etag": self.etag},
                tier_used=tier, not_modified=True,
            )
        return FetchResult(
            url=url, final_url=url, status=200, html=_BIG_PAGE,
            headers={"content-type": "text/html", "etag": self.etag}, tier_used=tier,
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


@pytest.mark.asyncio
async def test_crawl_shares_one_selector_cache(isolated_config, monkeypatch):
    """crawl() must build a single SelectorCache and reuse it for every page."""
    from scrapo import api

    _stub_router(monkeypatch, FakeHttp())

    built: list[object] = []
    real_cache = api.SelectorCache

    class CountingCache(real_cache):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            built.append(self)

    monkeypatch.setattr(api, "SelectorCache", CountingCache)

    seen: list[object] = []
    real_scrape = api.scrape

    async def spy_scrape(*a, **kw):
        seen.append(kw.get("selector_cache"))
        return await real_scrape(*a, **kw)

    monkeypatch.setattr(api, "scrape", spy_scrape)

    await api.crawl(
        ["https://example.com/"], schema=TinyDoc, config=isolated_config, max_depth=0,
        budget=Budget(max_tier=Tier.HTTP),
    )

    # exactly one SelectorCache built for the whole crawl
    assert len(built) == 1
    # and that same instance is threaded into each per-page scrape() call
    assert seen and all(c is built[0] for c in seen)


@pytest.mark.asyncio
async def test_rescrape_uses_conditional_get_and_reuses_archive(isolated_config, monkeypatch):
    http = ConditionalHttp()
    _stub_router(monkeypatch, http)
    budget = Budget(max_tier=Tier.HTTP)

    first = await scrape("https://example.com/watched", config=isolated_config, budget=budget)
    assert first.not_modified is False
    assert first.status == 200

    second = await scrape("https://example.com/watched", config=isolated_config, budget=budget)
    assert second.not_modified is True
    assert "Hello" in (second.markdown or "")  # body reconstructed from the archive
    assert http.conditionals_seen[0] is None  # first request: unconditional
    assert http.conditionals_seen[-1] is not None  # second request: conditional GET

    from scrapo.replay.store import ReplayStore

    runs = await ReplayStore(isolated_config).list_runs(url="https://example.com/watched", limit=2)
    assert runs[0]["not_modified"] == 1
    assert runs[0]["html_path"] == runs[1]["html_path"]  # no duplicate snapshot written
    assert runs[0]["etag"] == '"v1"'


@pytest.mark.asyncio
async def test_api_first_rewrites_wikipedia_to_rest_api(isolated_config, monkeypatch):
    http = RecordingHttp()
    _stub_router(monkeypatch, http)
    result = await scrape(
        "https://en.wikipedia.org/wiki/Albert_Einstein", config=isolated_config
    )
    # we hit the REST endpoint, not the bot-walled page
    assert http.fetched == [
        "https://en.wikipedia.org/api/rest_v1/page/html/Albert_Einstein"
    ]
    # ...but the result still presents the page the caller asked for, tagged via
    assert result.url == "https://en.wikipedia.org/wiki/Albert_Einstein"
    assert result.via == "api:wikipedia"
    assert result["via"] == "api:wikipedia"
    assert result.tier_used == "http"
    assert "Hello" in (result.markdown or "")


@pytest.mark.asyncio
async def test_api_first_off_per_call_scrapes_real_page(isolated_config, monkeypatch):
    http = RecordingHttp()
    _stub_router(monkeypatch, http)
    result = await scrape(
        "https://en.wikipedia.org/wiki/Albert_Einstein",
        config=isolated_config,
        api_first=False,
    )
    assert http.fetched == ["https://en.wikipedia.org/wiki/Albert_Einstein"]
    assert result.via is None
    assert "via" not in result  # None field reads as absent


@pytest.mark.asyncio
async def test_api_first_off_via_config(isolated_config, monkeypatch):
    http = RecordingHttp()
    _stub_router(monkeypatch, http)
    isolated_config.api_first = False
    result = await scrape("https://en.wikipedia.org/wiki/Dog", config=isolated_config)
    assert http.fetched == ["https://en.wikipedia.org/wiki/Dog"]
    assert result.via is None


@pytest.mark.asyncio
async def test_force_tier_bypasses_api_first(isolated_config, monkeypatch):
    http = RecordingHttp()
    _stub_router(monkeypatch, http)
    result = await scrape(
        "https://en.wikipedia.org/wiki/Cat", config=isolated_config, force_tier=Tier.HTTP
    )
    assert http.fetched == ["https://en.wikipedia.org/wiki/Cat"]
    assert result.via is None


@pytest.mark.asyncio
async def test_non_provider_url_is_untouched(isolated_config, monkeypatch):
    http = RecordingHttp()
    _stub_router(monkeypatch, http)
    result = await scrape("https://example.com/", config=isolated_config)
    assert http.fetched == ["https://example.com/"]
    assert result.via is None


@pytest.mark.asyncio
async def test_conditional_get_disabled_by_config(isolated_config, monkeypatch):
    http = ConditionalHttp()
    _stub_router(monkeypatch, http)
    isolated_config.conditional_requests = False
    budget = Budget(max_tier=Tier.HTTP)
    await scrape("https://example.com/nc", config=isolated_config, budget=budget)
    second = await scrape("https://example.com/nc", config=isolated_config, budget=budget)
    assert second.not_modified is False
    assert all(c is None for c in http.conditionals_seen)
