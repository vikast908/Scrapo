"""A rotating proxy pool with per-endpoint health tracking.

The vendor adapters in :mod:`scrapo.access.adapters` front a *managed* gateway
(Bright Data, Oxylabs, …) that rotates IPs for you. This pool is for the other
common shape: you hold a list of proxy URLs yourself and want Scrapo to spread
load across them, notice when one starts getting blocked, park it for a while,
and keep going on the rest.

``ProxyPool`` implements the :class:`~scrapo.access.adapters.base.ProxyAdapter`
protocol, so it drops straight into ``TierRouter`` / ``scrape()``. The HTTP and
browser tiers call :func:`report_outcome` after every fetch so the pool learns:
a 4xx auth/rate-limit code or an anti-bot fingerprint parks the proxy
immediately; a transient 5xx / network error counts toward ``max_failures``; a
clean fetch resets the streak. State is in-process only — like the browser pool,
it lives for the life of the ``TierRouter`` and is not persisted.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from scrapo.access.adapters.base import ProxyAdapter, ProxyConfig
from scrapo.types import FetchResult

log = structlog.get_logger(__name__)


@dataclass(slots=True)
class _Health:
    successes: int = 0
    failures: int = 0  # consecutive soft failures since the last success
    cooldown_until: float = 0.0


@dataclass
class ProxyPool:
    """Round-robin over ``urls``, skipping any endpoint currently in cooldown.

    Pass ``upstream`` (another adapter) to fall back to it when every static
    endpoint is parked; otherwise the pool returns ``None`` (direct connection)
    in that case rather than handing back a known-bad proxy.
    """

    urls: list[str]
    region: str | None = None
    cooldown_seconds: float = 120.0
    max_failures: int = 3
    upstream: ProxyAdapter | None = None
    name: str = "pool"
    _health: dict[str, _Health] = field(init=False, repr=False)
    _cursor: int = field(init=False, repr=False, default=0)
    _lock: asyncio.Lock = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.urls = [u.strip() for u in self.urls if u and u.strip()]
        self._health = {u: _Health() for u in self.urls}
        self._lock = asyncio.Lock()

    @classmethod
    def from_env(cls, *, cooldown_seconds: float | None = None) -> ProxyPool | None:
        """Build from ``SCRAPO_PROXY_URLS`` (comma-separated); ``None`` if unset."""
        raw = os.environ.get("SCRAPO_PROXY_URLS", "")
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        if not urls:
            return None
        cd = cooldown_seconds if cooldown_seconds is not None else float(
            os.environ.get("SCRAPO_PROXY_COOLDOWN", "120")
        )
        return cls(urls, cooldown_seconds=cd)

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        async with self._lock:
            now = time.monotonic()
            n = len(self.urls)
            for offset in range(n):
                idx = (self._cursor + offset) % n
                url = self.urls[idx]
                if self._health[url].cooldown_until <= now:
                    self._cursor = (idx + 1) % n
                    return ProxyConfig(url=url, region=geo or self.region, key=url)
        # every static endpoint is parked
        if self.upstream is not None:
            cfg = await self.upstream.get_proxy(geo or self.region)
            if cfg is not None and cfg.key is None:
                cfg.key = f"upstream:{getattr(self.upstream, 'name', 'adapter')}"
            return cfg
        log.warning("scrapo.proxy.all_parked", count=len(self.urls))
        return None

    async def report(self, key: str, *, ok: bool, hard: bool = False) -> None:
        """Record the outcome of a fetch that used proxy ``key``."""
        async with self._lock:
            h = self._health.get(key)
            if h is None:
                return  # an upstream proxy, or one we no longer track
            if ok:
                h.successes += 1
                h.failures = 0
                h.cooldown_until = 0.0
                return
            h.failures += 1
            if hard or h.failures >= self.max_failures:
                h.cooldown_until = time.monotonic() + self.cooldown_seconds
                log.info(
                    "scrapo.proxy.parked",
                    proxy=_redact(key),
                    seconds=self.cooldown_seconds,
                    failures=h.failures,
                    hard=hard,
                )

    def stats(self) -> list[dict[str, Any]]:
        """A snapshot of per-endpoint health, for debugging / introspection."""
        now = time.monotonic()
        return [
            {
                "proxy": _redact(url),
                "successes": h.successes,
                "failures": h.failures,
                "cooling_down": h.cooldown_until > now,
                "cooldown_remaining_s": max(0.0, round(h.cooldown_until - now, 1)),
            }
            for url, h in self._health.items()
        ]


def _is_hard_block(result: FetchResult) -> bool:
    """A block where the right move is to rotate this proxy away immediately.

    HTTP 4xx auth / rate-limit codes and anti-bot fingerprints (Cloudflare,
    DataDome, …) are IP-level — keep hammering the same proxy and it stays
    burned. Transient 5xx, network errors, and "the page just didn't render"
    aren't the proxy's fault, so they only count toward ``max_failures``.
    """
    if not result.blocked:
        return False
    reason = result.block_reason or ""
    return not (reason.startswith(("http-5", "network:")) or reason == "empty-body")


async def report_outcome(adapter: Any, pcfg: ProxyConfig | None, result: FetchResult) -> None:
    """Feed a :class:`FetchResult` back to a pool-like adapter, if it is one.

    No-op when ``adapter`` has no ``report`` method or ``pcfg`` carries no key,
    so it is safe to call unconditionally from every tier.
    """
    report = getattr(adapter, "report", None)
    if report is None or pcfg is None or not getattr(pcfg, "key", None):
        return
    hard = _is_hard_block(result)
    ok = not result.blocked and result.status != 0 and result.status < 500
    with contextlib.suppress(Exception):
        await report(pcfg.key, ok=ok, hard=hard)


def _redact(url: str) -> str:
    """Drop credentials from a proxy URL before logging it."""
    if "@" in url:
        scheme, _, rest = url.partition("://")
        host = rest.rsplit("@", 1)[-1]
        return f"{scheme}://{host}" if scheme else host
    return url
