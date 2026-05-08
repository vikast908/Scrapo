"""Zyte API proxy adapter — uses the smartproxy endpoint with API key as username."""

from __future__ import annotations

import os

from scrapo.access.adapters.base import ProxyConfig, register


class ZyteAdapter:
    name = "zyte"

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ZYTE_API_KEY", "")

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        if not self.api_key:
            return None
        host = "api.zyte.com:8011"
        return ProxyConfig(
            url=f"http://{self.api_key}:@{host}",
            region=geo,
            extra_headers={"Zyte-Geolocation": geo} if geo else None,
        )


def register_default() -> None:
    register(ZyteAdapter())
