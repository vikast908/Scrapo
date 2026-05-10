"""A lazily-launched, reused Playwright browser.

Launching Chromium costs roughly a second or two. When a :class:`TierRouter` is
reused across many fetches (most importantly during a crawl), this keeps one
browser process alive and hands out a fresh context per fetch instead of
cold-launching every time. It stays lazy: the browser is launched the first time
a browser-tier fetch actually runs, never on import or construction.

The browser is launched without a proxy; proxy settings (which may rotate per
fetch) are applied at the context level, so a single pooled browser can serve
every proxy.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


class BrowserPool:
    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._browser: Any = None

    async def _ensure_browser(self) -> Any:
        if self._browser is not None:
            return self._browser
        async with self._lock:
            if self._browser is not None:
                return self._browser
            from playwright.async_api import async_playwright

            pw = await async_playwright().start()
            try:
                browser = await pw.chromium.launch(headless=self._headless)
            except Exception:
                await pw.stop()
                raise
            self._pw = pw
            self._browser = browser
            return self._browser

    @asynccontextmanager
    async def context(self, **context_kwargs: Any) -> AsyncIterator[Any]:
        browser = await self._ensure_browser()
        ctx = await browser.new_context(**context_kwargs)
        try:
            yield ctx
        finally:
            await ctx.close()

    async def aclose(self) -> None:
        if self._browser is not None:
            try:
                await self._browser.close()
            finally:
                self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            finally:
                self._pw = None
