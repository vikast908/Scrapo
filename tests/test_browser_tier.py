"""BrowserTier.fetch behavior, exercised against a fake Playwright.

Launching real Chromium is out of scope for the offline suite; these fakes
mirror the small slice of the Playwright API the tier touches (context,
page, route, response) so the fetch path - proxy plumbing, stealth, resource
blocking, XHR capture, nav/launch failure reporting - is covered without it.
"""

from __future__ import annotations

import sys
import types
from contextlib import asynccontextmanager

import pytest

from scrapo.access.adapters.base import ProxyConfig
from scrapo.access.browser_tier import (
    BrowserTier,
    _block_heavy_resources,
    _make_xhr_capturer,
)
from scrapo.config import Config
from scrapo.types import Tier


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / "scrapo")


@pytest.fixture
def fake_playwright(monkeypatch):
    """Make the availability check (``import playwright.async_api``) pass even
    on machines without Playwright installed."""
    pkg = types.ModuleType("playwright")
    api = types.ModuleType("playwright.async_api")
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright.async_api", api)


# --- fakes ------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {"content-type": "text/html"}


class FakePage:
    def __init__(self, html="<html><body>hello world</body></html>", goto_exc=None):
        self._html = html
        self._goto_exc = goto_exc
        self.url = "about:blank"
        self.routes = []
        self.listeners = []
        self.waited_for = []
        self.goto_calls = []

    async def route(self, pattern, handler):
        self.routes.append((pattern, handler))

    def on(self, event, handler):
        self.listeners.append((event, handler))

    async def goto(self, url, **kwargs):
        self.goto_calls.append((url, kwargs))
        if self._goto_exc is not None:
            raise self._goto_exc
        self.url = url
        return FakeResponse()

    async def wait_for_selector(self, selector, **kwargs):
        self.waited_for.append(selector)

    async def content(self):
        return self._html

    async def screenshot(self, **kwargs):
        return b"\x89PNG-fake"


class FakeContext:
    def __init__(self, page):
        self._page = page
        self.cookies_added = []

    async def add_cookies(self, cookies):
        self.cookies_added.extend(cookies)

    async def new_page(self):
        return self._page


class FakePool:
    def __init__(self, page, *, enter_exc=None):
        self._page = page
        self._enter_exc = enter_exc
        self.context_kwargs = None
        self.closed = False

    @asynccontextmanager
    async def context(self, **kwargs):
        if self._enter_exc is not None:
            raise self._enter_exc
        self.context_kwargs = kwargs
        yield FakeContext(self._page)

    async def aclose(self):
        self.closed = True


class RecordingAdapter:
    name = "recording"

    def __init__(self, pcfg):
        self._pcfg = pcfg
        self.get_proxy_calls = []
        self.reports = []

    async def get_proxy(self, geo=None):
        self.get_proxy_calls.append(geo)
        return self._pcfg

    async def report(self, key, *, ok, hard=False):
        self.reports.append({"key": key, "ok": ok, "hard": hard})


def make_tier(cfg, page=None, *, adapter=None, enter_exc=None):
    tier = BrowserTier(cfg, proxy_adapter=adapter)
    tier._pool = FakePool(page or FakePage(), enter_exc=enter_exc)
    return tier


# --- fetch path ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_rejects_non_browser_tiers(cfg):
    tier = BrowserTier(cfg)
    with pytest.raises(ValueError, match="BrowserTier only handles"):
        await tier.fetch("https://example.com/", tier=Tier.HTTP)


@pytest.mark.asyncio
async def test_playwright_missing_returns_blocked(cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)  # forces ImportError
    tier = BrowserTier(cfg)
    result = await tier.fetch("https://example.com/")
    assert result.blocked
    assert result.block_reason.startswith("playwright-missing:")
    assert result.status == 0


@pytest.mark.asyncio
async def test_successful_fetch(cfg, fake_playwright):
    page = FakePage(html="<html><body>" + "real content " * 30 + "</body></html>")
    tier = make_tier(cfg, page)
    result = await tier.fetch("https://example.com/app")
    assert not result.blocked
    assert result.status == 200
    assert "real content" in result.html
    assert result.final_url == "https://example.com/app"
    assert result.tier_used is Tier.BROWSER
    assert result.elapsed_ms > 0
    # navigation honored the configured timeout
    (url, kwargs), = page.goto_calls
    assert kwargs["timeout"] == cfg.request_timeout * 1000


@pytest.mark.asyncio
async def test_nav_error_wins_over_body_classification(cfg, fake_playwright):
    page = FakePage(goto_exc=TimeoutError("nav timed out"))
    tier = make_tier(cfg, page)
    result = await tier.fetch("https://example.com/")
    assert result.blocked
    # the nav failure must be reported, not annotate()'s "empty-body" verdict
    assert result.block_reason == "browser-nav:TimeoutError"
    assert result.status == 0
    assert result.html == ""


@pytest.mark.asyncio
async def test_launch_error_returns_blocked_without_proxy_report(cfg, fake_playwright):
    adapter = RecordingAdapter(ProxyConfig(url="http://p:8080", key="p"))
    tier = make_tier(cfg, adapter=adapter, enter_exc=RuntimeError("no chromium binary"))
    result = await tier.fetch("https://example.com/")
    assert result.blocked
    assert result.block_reason == "browser-launch:RuntimeError"
    assert adapter.reports == []  # local fault; the proxy's health is untouched


@pytest.mark.asyncio
async def test_rendered_block_page_is_detected(cfg, fake_playwright):
    page = FakePage(html="<html><body>Verify with hCaptcha to continue</body></html>")
    tier = make_tier(cfg, page)
    result = await tier.fetch("https://example.com/")
    assert result.blocked
    assert result.block_reason == "captcha"


@pytest.mark.asyncio
async def test_wait_for_screenshot_and_cookies(cfg, fake_playwright):
    page = FakePage()
    tier = make_tier(cfg, page)
    cookies = [{"name": "sid", "value": "1", "url": "https://example.com"}]
    result = await tier.fetch(
        "https://example.com/", wait_for="#content", screenshot=True, cookies=cookies
    )
    assert page.waited_for == ["#content"]
    assert result.screenshot_png == b"\x89PNG-fake"


@pytest.mark.asyncio
async def test_storage_state_and_viewport_in_context_kwargs(cfg, fake_playwright):
    tier = make_tier(cfg)
    await tier.fetch("https://example.com/", storage_state="state.json")
    kwargs = tier._pool.context_kwargs
    assert kwargs["storage_state"] == "state.json"
    assert kwargs["user_agent"] == cfg.user_agent
    assert kwargs["viewport"] == {"width": 1366, "height": 900}


@pytest.mark.asyncio
async def test_resource_blocking_and_xhr_capture_toggle(cfg, fake_playwright):
    page = FakePage()
    tier = make_tier(cfg, page)
    await tier.fetch("https://example.com/")
    assert page.routes, "browser_block_resources=True should install an interceptor"
    assert any(event == "response" for event, _ in page.listeners)

    cfg2 = Config(data_dir=cfg.data_dir, browser_block_resources=False, browser_capture_xhr=False)
    page2 = FakePage()
    tier2 = make_tier(cfg2, page2)
    await tier2.fetch("https://example.com/")
    assert page2.routes == []
    assert page2.listeners == []


# --- proxy plumbing -----------------------------------------------------------


@pytest.mark.asyncio
async def test_proxy_settings_and_outcome_report(cfg, fake_playwright):
    pcfg = ProxyConfig(url="http://user:secret@proxy.example:3128", region="de", key="p1")
    adapter = RecordingAdapter(pcfg)
    tier = make_tier(cfg, adapter=adapter)
    result = await tier.fetch("https://example.com/", geo="de")
    assert adapter.get_proxy_calls == ["de"]
    assert tier._pool.context_kwargs["proxy"] == {
        "server": "http://proxy.example:3128",
        "username": "user",
        "password": "secret",
    }
    assert result.proxy_region == "de"
    assert adapter.reports == [{"key": "p1", "ok": True, "hard": False}]


def test_parse_proxy_without_port_omits_it():
    assert BrowserTier._parse_proxy("http://proxy.example") == {"server": "http://proxy.example"}


def test_parse_proxy_with_port_and_credentials():
    out = BrowserTier._parse_proxy("socks5://u:p@host:1080")
    assert out == {"server": "socks5://host:1080", "username": "u", "password": "p"}


# --- stealth ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stealth_applied_only_on_stealth_tier(cfg, fake_playwright, monkeypatch):
    calls = []

    async def stealth_async(page):
        calls.append(page)

    mod = types.ModuleType("playwright_stealth")
    mod.stealth_async = stealth_async
    monkeypatch.setitem(sys.modules, "playwright_stealth", mod)

    tier = make_tier(cfg, FakePage())
    await tier.fetch("https://example.com/", tier=Tier.BROWSER)
    assert calls == []

    tier2 = make_tier(cfg, FakePage())
    result = await tier2.fetch("https://example.com/", tier=Tier.BROWSER_STEALTH)
    assert len(calls) == 1
    assert result.tier_used is Tier.BROWSER_STEALTH


@pytest.mark.asyncio
async def test_stealth_plugin_missing_is_tolerated(cfg, fake_playwright, monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright_stealth", None)
    tier = make_tier(cfg, FakePage())
    result = await tier.fetch("https://example.com/", tier=Tier.BROWSER_STEALTH)
    assert not result.blocked  # stealth is best-effort, never fatal


# --- interception helpers -------------------------------------------------------


class FakeRoute:
    def __init__(self, resource_type, *, abort_exc=None):
        self.request = types.SimpleNamespace(resource_type=resource_type)
        self._abort_exc = abort_exc
        self.aborted = False
        self.continued = False

    async def abort(self):
        if self._abort_exc is not None:
            raise self._abort_exc
        self.aborted = True

    async def continue_(self):
        self.continued = True


@pytest.mark.asyncio
async def test_heavy_resources_aborted_documents_continue():
    image = FakeRoute("image")
    await _block_heavy_resources(image)
    assert image.aborted and not image.continued

    doc = FakeRoute("document")
    await _block_heavy_resources(doc)
    assert doc.continued and not doc.aborted


@pytest.mark.asyncio
async def test_block_helper_falls_back_to_continue_on_abort_failure():
    route = FakeRoute("font", abort_exc=RuntimeError("already handled"))
    await _block_heavy_resources(route)
    assert route.continued


class FakeXhrResponse:
    def __init__(self, resource_type="xhr", ctype="application/json", body=None, url="https://api.example/x"):
        self.request = types.SimpleNamespace(resource_type=resource_type)
        self.headers = {"content-type": ctype}
        self.status = 200
        self.url = url
        self._body = body if body is not None else {"ok": True}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_xhr_capturer_keeps_json_xhr_only():
    sink = []
    capture = _make_xhr_capturer(sink)
    await capture(FakeXhrResponse())
    await capture(FakeXhrResponse(resource_type="document"))
    await capture(FakeXhrResponse(ctype="text/html"))
    assert len(sink) == 1
    assert sink[0]["json"] == {"ok": True}
    assert sink[0]["status"] == 200


@pytest.mark.asyncio
async def test_xhr_capturer_caps_at_50():
    sink = []
    capture = _make_xhr_capturer(sink)
    for i in range(60):
        await capture(FakeXhrResponse(url=f"https://api.example/{i}"))
    assert len(sink) == 50
