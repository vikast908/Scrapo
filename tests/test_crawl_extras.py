import httpx
import pytest
import respx

from scrapo.crawl.scheduler import _next_link
from scrapo.crawl.sitemap import discover_sitemap_urls
from scrapo.results import ScrapeResult
from scrapo.types import FetchResult, Tier


def test_next_link_from_link_rel():
    html = '<html><head><link rel="next" href="/page/2"></head><body>x</body></html>'
    assert _next_link("https://e.com/page/1", html) == "https://e.com/page/2"


def test_next_link_from_anchor_rel():
    html = '<html><body><a rel="next" href="https://e.com/p3">Next</a></body></html>'
    assert _next_link("https://e.com/p2", html) == "https://e.com/p3"


def test_next_link_absent():
    assert _next_link("https://e.com/", "<html><body>last page</body></html>") is None
    assert _next_link("https://e.com/", "") is None


_NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'


@pytest.mark.asyncio
@respx.mock
async def test_sitemap_urls_discovered():
    sm = f'<?xml version="1.0"?><urlset {_NS}><url><loc>https://e.com/a</loc></url><url><loc>https://e.com/b</loc></url></urlset>'
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(200, text=sm))
    assert await discover_sitemap_urls("https://e.com", user_agent="t") == ["https://e.com/a", "https://e.com/b"]


@pytest.mark.asyncio
@respx.mock
async def test_sitemap_index_is_followed():
    idx = f'<?xml version="1.0"?><sitemapindex {_NS}><sitemap><loc>https://e.com/sm1.xml</loc></sitemap></sitemapindex>'
    sm1 = f'<?xml version="1.0"?><urlset {_NS}><url><loc>https://e.com/x</loc></url></urlset>'
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(200, text=idx))
    respx.get("https://e.com/sm1.xml").mock(return_value=httpx.Response(200, text=sm1))
    assert await discover_sitemap_urls("https://e.com", user_agent="t") == ["https://e.com/x"]


@pytest.mark.asyncio
@respx.mock
async def test_missing_sitemap_returns_empty():
    respx.get("https://e.com/sitemap.xml").mock(return_value=httpx.Response(404))
    assert await discover_sitemap_urls("https://e.com", user_agent="t") == []


_PAGE = "<!doctype html><html><head><title>P</title></head><body><main><h1>Hi</h1><p>" + ("ok " * 200) + "</p></main></body></html>"


@pytest.mark.asyncio
async def test_crawl_stream_yields_pages(isolated_config, monkeypatch):
    from scrapo import api

    class FakeHttp:
        async def fetch(self, url, **kwargs):
            return FetchResult(
                url=url, final_url=url, status=200, html=_PAGE,
                headers={"content-type": "text/html"}, tier_used=Tier.HTTP,
            )

    class StubRouter(api.TierRouter):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.http = FakeHttp()
            self.browser = FakeHttp()

    monkeypatch.setattr(api, "TierRouter", StubRouter)

    seen: list[ScrapeResult] = []
    async for result in api.crawl_stream(["https://example.com/"], config=isolated_config, max_depth=0):
        seen.append(result)
    assert len(seen) == 1
    assert isinstance(seen[0], ScrapeResult)
    assert "Hi" in (seen[0].markdown or "")
