"""Bright Data proxy adapter.

Reads BRIGHTDATA_USERNAME / BRIGHTDATA_PASSWORD / BRIGHTDATA_HOST from the env.
The Bright Data username encodes session and country: user-{user}-country-{cc}-session-{id}.
"""

from __future__ import annotations

import os
import uuid

from scrapo.access.adapters.base import ProxyAdapter, ProxyConfig, register


class BrightDataAdapter:
    name = "brightdata"

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        host: str | None = None,
    ) -> None:
        self.username = username or os.environ.get("BRIGHTDATA_USERNAME", "")
        self.password = password or os.environ.get("BRIGHTDATA_PASSWORD", "")
        self.host = host or os.environ.get("BRIGHTDATA_HOST", "brd.superproxy.io:22225")

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        if not self.username or not self.password:
            return None
        session = uuid.uuid4().hex[:12]
        user = self.username
        if geo:
            user = f"{user}-country-{geo.lower()}"
        user = f"{user}-session-{session}"
        return ProxyConfig(
            url=f"http://{user}:{self.password}@{self.host}",
            region=geo,
            sticky_session=session,
        )


def register_default() -> None:
    register(BrightDataAdapter())


__all__: list[str] = ["BrightDataAdapter", "register_default"]
_ = ProxyAdapter  # type marker
