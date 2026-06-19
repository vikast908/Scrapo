"""Site-map / URL discovery — the "map" feature.

Enumerate the URLs reachable on a site *without* scraping or converting their
content. Two sources are combined:

  1. ``sitemap.xml`` (and any sitemap-index it points at), via
     :func:`scrapo.crawl.sitemap.discover_sitemap_urls`.
  2. Breadth-first ``<a href>`` link discovery from the seeds, up to ``max_depth``.

Everything is best-effort and non-raising: a single failed page or sitemap just
yields fewer URLs. URLs are normalized, de-duplicated, scope-filtered (same host,
treating a leading ``www.`` as equivalent), SSRF-checked, and binary/asset URLs
are dropped entirely.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from urllib.parse import urljoin, urlparse, urlsplit

import httpx
import structlog
from selectolax.parser import HTMLParser

from scrapo.config import Config, get_config
from scrapo.crawl.dedup import UrlDeduper, normalize_url
from scrapo.crawl.sitemap import discover_sitemap_urls
from scrapo.security import SsrfError, is_url_allowed, safe_get

log = structlog.get_logger(__name__)

# Mirror the scheduler's binary/asset skip list: these are not pages worth
# enumerating, so they are dropped from the output and never followed.
_SKIP_LINK_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp",
    ".css", ".js", ".mjs", ".json", ".xml", ".rss", ".atom",
    ".zip", ".gz", ".tar", ".bz2", ".7z", ".rar",
    ".mp4", ".webm", ".mov", ".avi", ".mp3", ".wav", ".ogg", ".flac",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)

FetchHtml = Callable[[str], Awaitable[str | None]]


def _canonical_host(netloc: str) -> str:
    """Lowercased host with a leading ``www.`` stripped, so ``example.com`` and
    ``www.example.com`` are treated as the same host for scope checks. Does NOT
    broaden to arbitrary subdomains."""
    host = netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _is_mappable(url: str, *, allow_private: bool) -> bool:
    """A URL is worth keeping if it is an http(s) page, not a binary/asset, and
    passes the SSRF guard."""
    if not url.startswith(("http://", "https://")):
        return False
    if urlsplit(url).path.lower().endswith(_SKIP_LINK_EXTENSIONS):
        return False
    return is_url_allowed(url, allow_private=allow_private)


def _extract_links(base_url: str, html: str) -> list[str]:
    """Extract absolute, resolved ``<a href>`` targets from ``html``."""
    if not html:
        return []
    tree = HTMLParser(html)
    out: list[str] = []
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip() if a.attributes else ""
        if not href or href.startswith(("javascript:", "mailto:", "tel:", "#", "data:")):
            continue
        absu = urljoin(base_url, href)
        if absu.startswith(("http://", "https://")):
            out.append(absu)
    return out


def _make_default_fetcher(config: Config) -> FetchHtml:
    """Build a real HTML fetcher backed by ``safe_get`` (per-hop SSRF validation).

    Any fetch error — SSRF block, transport error, non-200, non-HTML — yields
    ``None`` so a single bad page never aborts the map.
    """

    async def _fetch(url: str) -> str | None:
        headers = {"User-Agent": config.user_agent}
        try:
            async with httpx.AsyncClient(
                timeout=config.request_timeout,
                headers=headers,
                follow_redirects=False,
            ) as client:
                resp = await safe_get(
                    client, url, allow_private=config.allow_private_hosts
                )
        except (SsrfError, httpx.HTTPError) as e:
            log.debug("scrapo.map.fetch_failed", url=url, err=str(e))
            return None
        if resp.status_code != 200:
            return None
        content_type = resp.headers.get("content-type", "")
        if content_type and "html" not in content_type.lower():
            return None
        return resp.text

    return _fetch


async def map_site(
    seeds: list[str],
    *,
    config: Config | None = None,
    max_urls: int = 5000,
    max_depth: int = 2,
    same_host_only: bool = True,
    use_sitemap: bool = True,
    fetch_html: FetchHtml | None = None,
) -> list[str]:
    """Discover URLs reachable from ``seeds`` without scraping their content.

    Returns a sorted, de-duplicated list of discovered URLs. Combines sitemap
    enumeration with breadth-first ``<a href>`` link discovery. Best-effort:
    never raises from a single bad page or sitemap.
    """
    config = config or get_config()
    fetch = fetch_html or _make_default_fetcher(config)
    allow_private = config.allow_private_hosts

    seed_hosts = {
        _canonical_host(urlparse(s).netloc) for s in seeds if urlparse(s).netloc
    }

    dedup = UrlDeduper()
    discovered: list[str] = []

    def _in_scope(url: str) -> bool:
        if not same_host_only:
            return True
        return _canonical_host(urlparse(url).netloc) in seed_hosts

    async def _accept(url: str) -> str | None:
        """Normalize, scope-check, SSRF/binary-filter and dedup ``url``.

        Returns the normalized URL if it was newly added to the output (and is
        worth following), else ``None``.
        """
        if len(discovered) >= max_urls:
            return None
        norm = normalize_url(url)
        if not _is_mappable(norm, allow_private=allow_private):
            return None
        if not _in_scope(norm):
            return None
        if not await dedup.add(norm):
            return None
        discovered.append(norm)
        return norm

    # 1. Sitemap enumeration for each distinct origin among the seeds.
    if use_sitemap:
        origins = {
            f"{p.scheme}://{p.netloc}"
            for p in (urlparse(s) for s in seeds)
            if p.scheme and p.netloc
        }
        for origin in sorted(origins):
            if len(discovered) >= max_urls:
                break
            try:
                sm_urls = await discover_sitemap_urls(
                    origin,
                    user_agent=config.user_agent,
                    request_timeout=config.request_timeout,
                    max_urls=max_urls,
                )
            except Exception as e:  # noqa: BLE001 - best-effort; one bad origin must not abort the map
                log.debug("scrapo.map.sitemap_failed", origin=origin, err=str(e))
                continue
            for u in sm_urls:
                await _accept(u)

    # 2. BFS link discovery from the seeds, level by level up to ``max_depth``.
    #    Seeds are the depth-0 frontier; their links are depth 1, and so on.
    frontier: list[str] = []
    for seed in seeds:
        accepted = await _accept(seed)
        if accepted is not None:
            frontier.append(accepted)

    depth = 0
    while frontier and depth < max_depth and len(discovered) < max_urls:
        next_frontier: list[str] = []
        for page_url in frontier:
            if len(discovered) >= max_urls:
                break
            try:
                html = await fetch(page_url)
            except Exception as e:  # noqa: BLE001 - best-effort; a bad fetch must not abort the map
                log.debug("scrapo.map.fetch_error", url=page_url, err=str(e))
                continue
            if not html:
                continue
            for link in _extract_links(page_url, html):
                accepted = await _accept(link)
                if accepted is not None:
                    next_frontier.append(accepted)
        frontier = next_frontier
        depth += 1

    return sorted(discovered)
