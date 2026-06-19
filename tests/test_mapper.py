"""Offline tests for the site-map / URL-discovery module.

All network I/O is injected via ``fetch_html`` and ``discover_sitemap_urls`` is
monkeypatched on the mapper module's reference, so these tests never touch the
network. ``asyncio_mode = auto`` (see pyproject) lets plain ``async def test_*``
work without a per-function marker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from scrapo.config import Config
from scrapo.crawl import mapper
from scrapo.crawl.mapper import map_site


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(data_dir=tmp_path / "scrapo")


def _fetcher(pages: dict[str, str]) -> Callable[[str], Awaitable[str | None]]:
    """Build an injectable fetcher backed by an in-memory ``url -> html`` map.

    The lookup is done on the *normalized* URL so test pages can be keyed by
    their canonical form (e.g. trailing slash on the path).
    """
    from scrapo.crawl.dedup import normalize_url

    async def _fetch(url: str) -> str | None:
        return pages.get(normalize_url(url))

    return _fetch


def _no_sitemap(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(origin: str, **kwargs: object) -> list[str]:
        return []

    monkeypatch.setattr(mapper, "discover_sitemap_urls", _empty)


def _link(href: str) -> str:
    return f'<html><body><a href="{href}">x</a></body></html>'


async def test_bfs_respects_max_depth(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    _no_sitemap(monkeypatch)
    pages = {
        "https://example.com/": '<html><body><a href="/a">a</a></body></html>',
        "https://example.com/a": _link("/b"),
        "https://example.com/b": _link("/c"),  # depth 3 — must be excluded
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=2,
        fetch_html=_fetcher(pages),
    )
    assert "https://example.com/a" in urls  # depth 1
    assert "https://example.com/b" in urls  # depth 2
    assert "https://example.com/c" not in urls  # depth 3 — beyond max_depth


async def test_depth_zero_only_returns_seed(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    pages = {"https://example.com/": _link("/a")}
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=0,
        fetch_html=_fetcher(pages),
    )
    assert urls == ["https://example.com/"]


async def test_same_host_only_excludes_other_hosts(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    pages = {
        "https://example.com/": (
            '<html><body>'
            '<a href="/internal">in</a>'
            '<a href="https://other.com/page">out</a>'
            "</body></html>"
        )
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        same_host_only=True,
        fetch_html=_fetcher(pages),
    )
    assert "https://example.com/internal" in urls
    assert "https://other.com/page" not in urls


async def test_same_host_treats_www_as_equivalent(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    # Seed is bare host; a link points at the www. variant — must be kept.
    pages = {
        "https://example.com/": _link("https://www.example.com/welcome"),
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        same_host_only=True,
        fetch_html=_fetcher(pages),
    )
    assert "https://www.example.com/welcome" in urls


async def test_same_host_off_keeps_other_hosts(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    pages = {"https://example.com/": _link("https://other.com/page")}
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        same_host_only=False,
        fetch_html=_fetcher(pages),
    )
    assert "https://other.com/page" in urls


async def test_sitemap_urls_merged_and_deduped(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    async def _fake_sitemap(origin: str, **kwargs: object) -> list[str]:
        return [
            "https://example.com/from-sitemap",
            "https://example.com/a",  # also reachable via BFS -> deduped
        ]

    monkeypatch.setattr(mapper, "discover_sitemap_urls", _fake_sitemap)

    pages = {
        "https://example.com/": _link("/a"),
        "https://example.com/a": "<html><body>leaf</body></html>",
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=2,
        use_sitemap=True,
        fetch_html=_fetcher(pages),
    )
    assert "https://example.com/from-sitemap" in urls
    # /a appears once despite being in both the sitemap and the BFS crawl.
    assert urls.count("https://example.com/a") == 1


async def test_use_sitemap_false_skips_sitemap(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    called = False

    async def _fake_sitemap(origin: str, **kwargs: object) -> list[str]:
        nonlocal called
        called = True
        return ["https://example.com/sm"]

    monkeypatch.setattr(mapper, "discover_sitemap_urls", _fake_sitemap)
    pages = {"https://example.com/": "<html><body>x</body></html>"}
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        use_sitemap=False,
        fetch_html=_fetcher(pages),
    )
    assert called is False
    assert "https://example.com/sm" not in urls


async def test_max_urls_cap(monkeypatch: pytest.MonkeyPatch, config: Config) -> None:
    _no_sitemap(monkeypatch)
    body = "".join(f'<a href="/p{i}">p</a>' for i in range(50))
    pages = {"https://example.com/": f"<html><body>{body}</body></html>"}
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        max_urls=5,
        fetch_html=_fetcher(pages),
    )
    assert len(urls) == 5


async def test_fetcher_returning_none_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)

    async def _none(url: str) -> str | None:
        return None

    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=2,
        fetch_html=_none,
    )
    # The seed itself is still discovered; just no links beyond it.
    assert urls == ["https://example.com/"]


async def test_fetcher_raising_does_not_crash(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)

    async def _boom(url: str) -> str | None:
        raise RuntimeError("network on fire")

    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=2,
        fetch_html=_boom,
    )
    assert urls == ["https://example.com/"]


async def test_binary_extensions_skipped(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    pages = {
        "https://example.com/": (
            "<html><body>"
            '<a href="/doc.pdf">pdf</a>'
            '<a href="/img.jpg">jpg</a>'
            '<a href="/real-page">page</a>'
            "</body></html>"
        )
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        fetch_html=_fetcher(pages),
    )
    assert "https://example.com/real-page" in urls
    assert not any(u.endswith((".pdf", ".jpg")) for u in urls)


async def test_output_sorted_and_unique(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    pages = {
        "https://example.com/": (
            "<html><body>"
            '<a href="/zebra">z</a>'
            '<a href="/apple">a</a>'
            '<a href="/apple">a-dup</a>'  # duplicate
            '<a href="/mango">m</a>'
            "</body></html>"
        )
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        fetch_html=_fetcher(pages),
    )
    assert urls == sorted(urls)
    assert len(urls) == len(set(urls))
    assert urls == [
        "https://example.com/",
        "https://example.com/apple",
        "https://example.com/mango",
        "https://example.com/zebra",
    ]


async def test_ssrf_blocked_urls_dropped(
    monkeypatch: pytest.MonkeyPatch, config: Config
) -> None:
    _no_sitemap(monkeypatch)
    pages = {
        "https://example.com/": (
            "<html><body>"
            '<a href="http://169.254.169.254/latest/meta-data">metadata</a>'
            '<a href="/safe">safe</a>'
            "</body></html>"
        )
    }
    urls = await map_site(
        ["https://example.com/"],
        config=config,
        max_depth=1,
        same_host_only=False,
        fetch_html=_fetcher(pages),
    )
    assert "https://example.com/safe" in urls
    assert not any("169.254.169.254" in u for u in urls)
