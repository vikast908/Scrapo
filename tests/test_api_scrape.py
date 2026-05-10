"""End-to-end scrape() with the HTTP tier monkeypatched to a fixed page."""

from __future__ import annotations

from typing import Optional

import pytest
from pydantic import BaseModel

from scrapo.api import scrape
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
    title: Optional[str] = None


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
