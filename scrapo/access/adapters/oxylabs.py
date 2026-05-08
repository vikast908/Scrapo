"""Oxylabs proxy adapter."""

from __future__ import annotations

import os

from scrapo.access.adapters.base import ProxyConfig, register


class OxylabsAdapter:
    name = "oxylabs"

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        host: str | None = None,
    ) -> None:
        self.username = username or os.environ.get("OXYLABS_USERNAME", "")
        self.password = password or os.environ.get("OXYLABS_PASSWORD", "")
        self.host = host or os.environ.get("OXYLABS_HOST", "pr.oxylabs.io:7777")

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        if not self.username or not self.password:
            return None
        user = self.username
        if geo:
            user = f"customer-{user}-cc-{geo.lower()}"
        return ProxyConfig(
            url=f"http://{user}:{self.password}@{self.host}",
            region=geo,
        )


def register_default() -> None:
    register(OxylabsAdapter())
