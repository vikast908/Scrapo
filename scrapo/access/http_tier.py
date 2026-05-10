"""T0 / T1 - pure HTTP fetching with optional sessioned headers."""

from __future__ import annotations

import asyncio
import random
import time
from typing import Any

import httpx
import structlog

from scrapo.access.adapters.base import ProxyAdapter
from scrapo.access.signals import annotate
from scrapo.config import Config
from scrapo.types import FetchResult, Tier

log = structlog.get_logger(__name__)

_RETRYABLE_STATUS = {429, 500, 502, 503, 504}

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
    """Tier 0/1 fetcher - fast static-page path with bounded retries."""

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

        attempts = max(1, self.config.http_retries + 1)
        last: FetchResult | None = None
        async with httpx.AsyncClient(**client_kwargs) as client:
            for attempt in range(attempts):
                start = time.perf_counter()
                try:
                    resp = await client.get(url)
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    headers = dict(resp.headers)
                    ctype = (headers.get("content-type") or "").split(";")[0].strip().lower()
                    is_binary = ctype == "application/pdf" or ctype.startswith(
                        ("image/", "audio/", "video/", "font/")
                    ) or ctype in ("application/octet-stream", "application/zip", "application/gzip")
                    last = annotate(
                        FetchResult(
                            url=url,
                            final_url=str(resp.url),
                            status=resp.status_code,
                            html="" if is_binary else resp.text,
                            headers=headers,
                            tier_used=tier,
                            elapsed_ms=elapsed_ms,
                            proxy_region=proxy_region,
                            raw_content=resp.content if is_binary else None,
                        )
                    )
                    if resp.status_code not in _RETRYABLE_STATUS:
                        return last
                except httpx.HTTPError as e:
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    last = FetchResult(
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
                if attempt < attempts - 1:
                    backoff = min(8.0, 0.5 * (2**attempt)) + random.uniform(0, 0.4)  # noqa: S311
                    log.debug(
                        "scrapo.http.retry",
                        url=url,
                        attempt=attempt + 1,
                        status=last.status if last else None,
                        sleep=round(backoff, 2),
                    )
                    await asyncio.sleep(backoff)

        assert last is not None
        return last
