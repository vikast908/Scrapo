"""T0 / T1 - pure HTTP fetching with optional sessioned headers."""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from typing import Any

import httpx
import structlog

from scrapo.access.adapters.base import ProxyAdapter, ProxyConfig
from scrapo.access.proxy_pool import report_outcome
from scrapo.access.signals import annotate
from scrapo.config import Config
from scrapo.security import SsrfError, safe_get
from scrapo.types import Conditional, FetchResult, Tier

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
        # Cache one httpx.AsyncClient per proxy URL (None => the no-proxy client) so
        # connections / TLS sessions are reused across fetches to the same host.
        # Per-CLIENT settings (proxy, http2, timeout, redirects) live on the client;
        # per-REQUEST settings (headers, cookies) are passed to client.get(...).
        self._clients: dict[str | None, httpx.AsyncClient] = {}

    def _get_client(self, proxy_url: str | None) -> httpx.AsyncClient:
        client = self._clients.get(proxy_url)
        if client is None or client.is_closed:
            client_kwargs: dict[str, Any] = {
                "timeout": self.config.request_timeout,
                # Redirects are followed manually via security.safe_get so every hop
                # is re-validated against the SSRF guard.
                "follow_redirects": False,
                "http2": True,
            }
            if proxy_url:
                client_kwargs["proxy"] = proxy_url
            client = httpx.AsyncClient(**client_kwargs)
            self._clients[proxy_url] = client
        return client

    async def aclose(self) -> None:
        """Close all cached httpx clients. Safe to call repeatedly."""
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            with contextlib.suppress(Exception):  # best-effort teardown
                await client.aclose()

    async def fetch(
        self,
        url: str,
        *,
        tier: Tier = Tier.HTTP,
        cookies: dict[str, str] | None = None,
        extra_headers: dict[str, str] | None = None,
        geo: str | None = None,
        conditional: Conditional | None = None,
    ) -> FetchResult:
        if tier not in (Tier.HTTP, Tier.HTTP_SESSIONED):
            raise ValueError(f"HttpTier only handles HTTP/HTTP_SESSIONED, got {tier}")

        headers: dict[str, str] = {"User-Agent": self.config.user_agent}
        if tier is Tier.HTTP_SESSIONED:
            headers.update(_DEFAULT_HEADERS_T1)
        if extra_headers:
            headers.update(extra_headers)
        if conditional is not None and not conditional.is_empty:
            headers.update(conditional.headers())

        pcfg: ProxyConfig | None = None
        proxy_region: str | None = None
        if self.proxy_adapter:
            pcfg = await self.proxy_adapter.get_proxy(geo or self.config.geo)
            if pcfg:
                proxy_region = pcfg.region
                if pcfg.extra_headers:
                    headers.update(pcfg.extra_headers)

        client = self._get_client(pcfg.url if pcfg else None)
        allow_private = self.config.allow_private_hosts

        attempts = max(1, self.config.http_retries + 1)
        last: FetchResult | None = None
        for attempt in range(attempts):
            start = time.perf_counter()
            try:
                resp = await safe_get(
                    client,
                    url,
                    allow_private=allow_private,
                    headers=headers,
                    cookies=cookies,
                )
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                resp_headers = dict(resp.headers)
                if resp.status_code == 304:
                    # conditional GET hit: nothing changed, no body sent
                    last = FetchResult(
                        url=url,
                        final_url=str(resp.url),
                        status=304,
                        html="",
                        headers=resp_headers,
                        tier_used=tier,
                        elapsed_ms=elapsed_ms,
                        proxy_region=proxy_region,
                        not_modified=True,
                    )
                    break
                ctype = (resp_headers.get("content-type") or "").split(";")[0].strip().lower()
                is_binary = ctype == "application/pdf" or ctype.startswith(
                    ("image/", "audio/", "video/", "font/")
                ) or ctype in ("application/octet-stream", "application/zip", "application/gzip")
                last = annotate(
                    FetchResult(
                        url=url,
                        final_url=str(resp.url),
                        status=resp.status_code,
                        html="" if is_binary else resp.text,
                        headers=resp_headers,
                        tier_used=tier,
                        elapsed_ms=elapsed_ms,
                        proxy_region=proxy_region,
                        raw_content=resp.content if is_binary else None,
                    )
                )
                if resp.status_code not in _RETRYABLE_STATUS:
                    break
            except SsrfError as e:
                # A redirect hop pointed at a blocked target (or too many hops).
                # Surface as a blocked FetchResult instead of raising out of fetch().
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
                    block_reason=f"ssrf-redirect:{e}",
                )
                break
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
        await report_outcome(self.proxy_adapter, pcfg, last)
        return last
