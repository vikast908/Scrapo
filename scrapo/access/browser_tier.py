"""T2 / T3: Playwright-based browser fetch with optional stealth.

Uses a shared :class:`~scrapo.access.browser_pool.BrowserPool` so a router that
fetches many pages does not relaunch Chromium each time. Playwright (and the
stealth plugin) are imported lazily so the package installs without them.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any

import structlog

from scrapo.access.adapters.base import ProxyAdapter, ProxyConfig
from scrapo.access.browser_pool import BrowserPool
from scrapo.access.proxy_pool import report_outcome
from scrapo.access.signals import annotate
from scrapo.config import Config
from scrapo.types import FetchResult, Tier

log = structlog.get_logger(__name__)


class BrowserTier:
    """Tier 2/3 fetcher: JS rendering, optional stealth + residential proxy."""

    def __init__(self, config: Config, proxy_adapter: ProxyAdapter | None = None) -> None:
        self.config = config
        self.proxy_adapter = proxy_adapter
        self._pool: BrowserPool | None = None

    def _get_pool(self) -> BrowserPool:
        if self._pool is None:
            self._pool = BrowserPool(headless=True)
        return self._pool

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.aclose()
            self._pool = None

    async def fetch(
        self,
        url: str,
        *,
        tier: Tier = Tier.BROWSER,
        cookies: list[dict[str, Any]] | None = None,
        wait_for: str | None = None,
        screenshot: bool = False,
        geo: str | None = None,
        storage_state: str | None = None,
    ) -> FetchResult:
        if tier not in (Tier.BROWSER, Tier.BROWSER_STEALTH):
            raise ValueError(f"BrowserTier only handles BROWSER/BROWSER_STEALTH, got {tier}")

        try:
            import playwright.async_api  # noqa: F401 - availability check
        except ImportError as e:
            return FetchResult(
                url=url, final_url=url, status=0, html="", headers={}, tier_used=tier,
                blocked=True, block_reason=f"playwright-missing:{e}",
            )

        pcfg: ProxyConfig | None = (
            await self.proxy_adapter.get_proxy(geo or self.config.geo) if self.proxy_adapter else None
        )
        proxy_region = pcfg.region if pcfg else None
        proxy_settings = self._parse_proxy(pcfg.url) if pcfg else None

        ctx_kwargs: dict[str, Any] = {
            "user_agent": self.config.user_agent,
            "viewport": {"width": 1366, "height": 900},
        }
        if proxy_settings:
            ctx_kwargs["proxy"] = proxy_settings
        if storage_state:
            ctx_kwargs["storage_state"] = storage_state

        captured: list[dict[str, Any]] = []
        start = time.perf_counter()
        async with self._get_pool().context(**ctx_kwargs) as context:
            if cookies:
                await context.add_cookies(cookies)
            page = await context.new_page()
            if self.config.browser_block_resources:
                await page.route("**/*", _block_heavy_resources)
            if self.config.browser_capture_xhr:
                page.on("response", _make_xhr_capturer(captured))
            if tier is Tier.BROWSER_STEALTH:
                await self._apply_stealth(page)
            response = await page.goto(
                url, timeout=self.config.request_timeout * 1000, wait_until="domcontentloaded"
            )
            if wait_for:
                with contextlib.suppress(Exception):
                    await page.wait_for_selector(wait_for, timeout=10_000)

            html = await page.content()
            status = response.status if response else 0
            headers = dict(response.headers) if response else {}
            final_url = page.url
            shot = await page.screenshot(full_page=False) if screenshot else None
            elapsed_ms = (time.perf_counter() - start) * 1000.0

        result = annotate(
            FetchResult(
                url=url,
                final_url=final_url,
                status=status,
                html=html,
                headers=headers,
                tier_used=tier,
                elapsed_ms=elapsed_ms,
                proxy_region=proxy_region,
                screenshot_png=shot,
                captured_json=captured[:50],
            )
        )
        await report_outcome(self.proxy_adapter, pcfg, result)
        return result

    @staticmethod
    def _parse_proxy(proxy_url: str) -> dict[str, str]:
        from urllib.parse import urlparse

        parsed = urlparse(proxy_url)
        out: dict[str, str] = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
        if parsed.username:
            out["username"] = parsed.username
        if parsed.password:
            out["password"] = parsed.password
        return out

    @staticmethod
    async def _apply_stealth(page: Any) -> None:
        """Best-effort: patch the page to look less automated. Versions of the
        stealth plugin differ; try the common entry points and shrug off the rest."""
        with contextlib.suppress(Exception):
            from playwright_stealth import stealth_async

            await stealth_async(page)
            return
        with contextlib.suppress(Exception):
            from playwright_stealth import Stealth

            await Stealth().apply_stealth_async(page)


_HEAVY_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


async def _block_heavy_resources(route: Any) -> None:
    try:
        if route.request.resource_type in _HEAVY_RESOURCE_TYPES:
            await route.abort()
        else:
            await route.continue_()
    except Exception:
        with contextlib.suppress(Exception):
            await route.continue_()


def _make_xhr_capturer(sink: list[dict[str, Any]]) -> Any:
    async def _on_response(response: Any) -> None:
        if len(sink) >= 50:
            return
        try:
            if response.request.resource_type not in ("xhr", "fetch"):
                return
            ctype = (response.headers.get("content-type") or "").lower()
            if "json" not in ctype:
                return
            sink.append({"url": response.url, "status": response.status, "json": await response.json()})
        except Exception:
            return

    return _on_response
