"""T2 / T3 — Playwright-based browser fetch with optional stealth."""

from __future__ import annotations

import time
from typing import Any

from scrapo.access.adapters.base import ProxyAdapter
from scrapo.access.signals import annotate
from scrapo.config import Config
from scrapo.types import FetchResult, Tier


class BrowserTier:
    """Tier 2/3 fetcher — JS rendering, optional stealth + residential proxy.

    Imports Playwright lazily so the package installs without it.
    """

    def __init__(self, config: Config, proxy_adapter: ProxyAdapter | None = None) -> None:
        self.config = config
        self.proxy_adapter = proxy_adapter

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
            from playwright.async_api import async_playwright
        except ImportError as e:
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                html="",
                headers={},
                tier_used=tier,
                blocked=True,
                block_reason=f"playwright-missing:{e}",
            )

        proxy_settings: dict[str, str] | None = None
        proxy_region: str | None = None
        if self.proxy_adapter:
            cfg = await self.proxy_adapter.get_proxy(geo or self.config.geo)
            if cfg:
                proxy_region = cfg.region
                proxy_settings = self._parse_proxy(cfg.url)

        start = time.perf_counter()
        async with async_playwright() as pw:
            launch_kwargs: dict[str, Any] = {"headless": True}
            if proxy_settings:
                launch_kwargs["proxy"] = proxy_settings
            browser = await pw.chromium.launch(**launch_kwargs)
            try:
                ctx_kwargs: dict[str, Any] = {
                    "user_agent": self.config.user_agent,
                    "viewport": {"width": 1366, "height": 900},
                }
                if storage_state:
                    ctx_kwargs["storage_state"] = storage_state
                context = await browser.new_context(**ctx_kwargs)
                if cookies:
                    await context.add_cookies(cookies)

                if tier is Tier.BROWSER_STEALTH:
                    await self._apply_stealth(context)

                page = await context.new_page()
                response = await page.goto(
                    url,
                    timeout=self.config.request_timeout * 1000,
                    wait_until="domcontentloaded",
                )
                if wait_for:
                    try:
                        await page.wait_for_selector(wait_for, timeout=10_000)
                    except Exception:
                        pass

                html = await page.content()
                status = response.status if response else 0
                headers = dict(response.headers) if response else {}
                final_url = page.url
                shot = await page.screenshot(full_page=False) if screenshot else None
                elapsed_ms = (time.perf_counter() - start) * 1000.0

                result = FetchResult(
                    url=url,
                    final_url=final_url,
                    status=status,
                    html=html,
                    headers=headers,
                    tier_used=tier,
                    elapsed_ms=elapsed_ms,
                    proxy_region=proxy_region,
                    screenshot_png=shot,
                )
                await context.close()
                return annotate(result)
            finally:
                await browser.close()

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
    async def _apply_stealth(context: Any) -> None:
        try:
            from playwright_stealth import StealthConfig, stealth_async
        except ImportError:
            return
        # apply per-page on context creation
        async def _on_page(page: Any) -> None:
            await stealth_async(page, StealthConfig())
        context.on("page", lambda page: page.context.loop.create_task(_on_page(page)))
