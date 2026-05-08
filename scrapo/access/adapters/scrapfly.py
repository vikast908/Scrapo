"""Scrapfly proxy adapter — uses Scrapfly's proxy URL form."""

from __future__ import annotations

import os

from scrapo.access.adapters.base import ProxyConfig, register


class ScrapflyAdapter:
    name = "scrapfly"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("SCRAPFLY_API_KEY", "")

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        if not self.api_key:
            return None
        host = "proxy.scrapfly.io:8888"
        country = (geo or "us").lower()
        user = f"scrapfly-country-{country}-render-true"
        return ProxyConfig(
            url=f"http://{user}:{self.api_key}@{host}",
            region=country,
        )


def register_default() -> None:
    register(ScrapflyAdapter())
