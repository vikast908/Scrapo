"""T0 / T1 — pure HTTP fetching with optional sessioned headers."""

from __future__ import annotations

import time
from typing import Any

import httpx

from scrapo.access.adapters.base import ProxyAdapter
from scrapo.access.signals import annotate
from scrapo.config import Config
from scrapo.types import FetchResult, Tier

_DEFAULT_HEADERS_T1 = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


class HttpTier:
    """Tier 0/1 fetcher — fast static-page path."""

    def __init__(self, config: Config, proxy_adapter: ProxyAdapter | None = None) -> None:
        self.config = config
        self.proxy_adapter = proxy_adapter

    async def fetch(
        self,
        url: str,
        *,
        tier: Tier = Tier.HTTP,
        cookies: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        geo: str | None = None,
    ) -> FetchResult:
        if tier not in (Tier.HTTP, Tier.HTTP_SESSIONED):
            raise ValueError(f"HttpTier only handles HTTP/HTTP_SESSIONED, got {tier}")

        headers: dict[str, str] = {"User-Agent": self.config.user_agent}
        if tier is Tier.HTTP_SESSIONED:
            headers.update(_DEFAULT_HEADERS_T1)
        if extra_headers:
            headers.update(extra_headers)

        proxy_url: str | None = None
        proxy_region: str | None = None
        if self.proxy_adapter:
            cfg = await self.proxy_adapter.get_proxy(geo or self.config.geo)
            if cfg:
                proxy_url = cfg.url
                proxy_region = cfg.region
                if cfg.extra_headers:
                    headers.update(cfg.extra_headers)

        client_kwargs: dict[str, Any] = {
            "timeout": self.config.request_timeout,
            "follow_redirects": True,
            "headers": headers,
            "http2": True,
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        if cookies:
            client_kwargs["cookies"] = cookies

        start = time.perf_counter()
        async with httpx.AsyncClient(**client_kwargs) as client:
            try:
                resp = await client.get(url)
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                result = FetchResult(
                    url=url,
                    final_url=str(resp.url),
                    status=resp.status_code,
                    html=resp.text,
                    headers=dict(resp.headers),
                    tier_used=tier,
                    elapsed_ms=elapsed_ms,
                    proxy_region=proxy_region,
                )
                return annotate(result)
            except httpx.HTTPError as e:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return FetchResult(
                    url=url,
                    final_url=url,
                    status=0,
                    html="",
                    headers={},
                    tier_used=tier,
                    elapsed_ms=elapsed_ms,
                    proxy_region=proxy_region,
                    blocked=True,
                    block_reason=f"network:{type(e).__name__}",
                )
