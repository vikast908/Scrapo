"""Router escalation logic — uses monkeypatched tiers, no real network."""

from __future__ import annotations

from typing import Any

import pytest

from scrapo.access.router import TierRouter
from scrapo.types import Budget, FetchResult, Tier


class StubTier:
    def __init__(self, results: dict[Tier, FetchResult]) -> None:
        self.results = results
        self.calls: list[Tier] = []

    async def fetch(self, url: str, **kwargs: Any) -> FetchResult:
        tier = kwargs.get("tier") or kwargs.get("force_tier") or Tier.HTTP
        self.calls.append(tier)
        return self.results[tier]


def _ok(tier: Tier, html: str = "<html><body>" + "x" * 600 + "</body></html>") -> FetchResult:
    return FetchResult(
        url="https://e.com/x", final_url="https://e.com/x",
        status=200, html=html, headers={}, tier_used=tier,
    )


def _blocked(tier: Tier, reason: str = "cloudflare") -> FetchResult:
    return FetchResult(
        url="https://e.com/x", final_url="https://e.com/x",
        status=200, html="<html><body>checking</body></html>", headers={},
        tier_used=tier, blocked=True, block_reason=reason,
    )


@pytest.mark.asyncio
async def test_router_returns_first_success(isolated_config):
    router = TierRouter(isolated_config)
    stub = StubTier({
        Tier.HTTP: _ok(Tier.HTTP),
        Tier.HTTP_SESSIONED: _ok(Tier.HTTP_SESSIONED),
        Tier.BROWSER: _ok(Tier.BROWSER),
    })
    router.http = stub
    router.browser = stub
    result = await router.fetch("https://e.com/x")
    assert result.tier_used == Tier.HTTP
    assert stub.calls == [Tier.HTTP]


@pytest.mark.asyncio
async def test_router_escalates_past_block(isolated_config):
    router = TierRouter(isolated_config)
    stub = StubTier({
        Tier.HTTP: _blocked(Tier.HTTP),
        Tier.HTTP_SESSIONED: _blocked(Tier.HTTP_SESSIONED, "akamai"),
        Tier.BROWSER: _ok(Tier.BROWSER),
        Tier.BROWSER_STEALTH: _ok(Tier.BROWSER_STEALTH),
    })
    router.http = stub
    router.browser = stub
    result = await router.fetch(
        "https://e.com/x", budget=Budget(max_tier=Tier.BROWSER)
    )
    assert result.tier_used == Tier.BROWSER
    assert Tier.HTTP in stub.calls
    assert Tier.BROWSER in stub.calls


def test_router_builds_proxy_pool_from_config(tmp_path):
    from scrapo.access.proxy_pool import ProxyPool
    from scrapo.config import Config

    r = TierRouter(Config(data_dir=tmp_path / "a", proxy_urls=["http://a:1", "http://b:2"]))
    assert isinstance(r.proxy_adapter, ProxyPool)
    assert r.http.proxy_adapter is r.proxy_adapter
    assert r.browser.proxy_adapter is r.proxy_adapter
    # nothing configured -> no proxy adapter at all
    assert TierRouter(Config(data_dir=tmp_path / "b")).proxy_adapter is None
    # an explicitly passed adapter wins; the static pool is not auto-built
    sentinel = object()
    assert TierRouter(
        Config(data_dir=tmp_path / "c", proxy_urls=["http://a:1"]), proxy_adapter=sentinel
    ).proxy_adapter is sentinel


@pytest.mark.asyncio
async def test_router_respects_max_tier_budget(isolated_config):
    router = TierRouter(isolated_config)
    stub = StubTier({
        Tier.HTTP: _blocked(Tier.HTTP),
        Tier.HTTP_SESSIONED: _blocked(Tier.HTTP_SESSIONED, "akamai"),
    })
    router.http = stub
    result = await router.fetch(
        "https://e.com/x", budget=Budget(max_tier=Tier.HTTP_SESSIONED)
    )
    assert result.blocked
    assert result.tier_used == Tier.HTTP_SESSIONED
