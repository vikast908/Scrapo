"""watch() / Watch.refresh() — uses a stubbed router, no network."""

from __future__ import annotations

import pytest

from scrapo.results import ScrapeResult
from scrapo.types import Budget, FetchResult, Tier
from scrapo.watch import ChangeSet, Watch, watch

_FILLER = "lorem ipsum dolor sit amet " * 20  # keep the page well above the "thin" threshold


class _StatefulServer:
    """A fake site whose body + ETag advance only when change() is called."""

    def __init__(self) -> None:
        self.version = 0

    @property
    def etag(self) -> str:
        return f'"v{self.version}"'

    @property
    def body(self) -> str:
        return f"<html><body><main><h1>v{self.version}</h1><p>{_FILLER}</p></main></body></html>"

    def change(self) -> None:
        self.version += 1

    async def fetch(self, url, *, tier=Tier.HTTP, conditional=None, **kwargs):
        if conditional is not None and conditional.etag == self.etag:
            return FetchResult(
                url=url, final_url=url, status=304, html="", headers={"etag": self.etag},
                tier_used=tier, not_modified=True,
            )
        return FetchResult(
            url=url, final_url=url, status=200, html=self.body,
            headers={"content-type": "text/html", "etag": self.etag}, tier_used=tier,
        )


def _stub_router(monkeypatch, server):
    from scrapo import api

    class StubRouter(api.TierRouter):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.http = server
            self.browser = server

    monkeypatch.setattr(api, "TierRouter", StubRouter)


@pytest.mark.asyncio
async def test_watch_first_scrape_then_unchanged_then_changed(isolated_config, monkeypatch):
    server = _StatefulServer()
    _stub_router(monkeypatch, server)
    budget = Budget(max_tier=Tier.HTTP)

    w = await watch("https://example.com/page", config=isolated_config, budget=budget)
    assert isinstance(w, Watch)
    assert isinstance(w.last, ScrapeResult)
    assert "v0" in (w.last.markdown or "")
    assert w.last_run_id

    # nothing changed — the conditional GET comes back 304
    cs1 = await w.refresh()
    assert isinstance(cs1, ChangeSet)
    assert cs1.not_modified is True
    assert cs1.changed is False
    assert "v0" in (cs1.result.markdown or "")
    assert "not modified" in cs1.summary()

    # the site changes — the next refresh notices
    server.change()
    cs2 = await w.refresh()
    assert cs2.not_modified is False
    assert cs2.changed is True
    assert "v1" in (cs2.result.markdown or "")
    assert cs2.diff is not None
    assert cs2.diff.same_html is False


@pytest.mark.asyncio
async def test_watch_check_does_not_diff(isolated_config, monkeypatch):
    server = _StatefulServer()
    _stub_router(monkeypatch, server)
    w = Watch(url="https://example.com/c", config=isolated_config, scrape_kwargs={"budget": Budget(max_tier=Tier.HTTP)})
    r = await w.check()
    assert isinstance(r, ScrapeResult)
    assert w.last_run_id == r.run_id
