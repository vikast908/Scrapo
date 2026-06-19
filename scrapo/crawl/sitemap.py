"""Fetch and parse ``sitemap.xml`` (and sitemap-index files).

Best-effort and non-raising: a missing, malformed, or huge sitemap just yields
fewer URLs. Follows one layer of ``<sitemapindex>``.
"""

from __future__ import annotations

import gzip
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx
import structlog

from scrapo.config import get_config
from scrapo.security import SsrfError, safe_get

log = structlog.get_logger(__name__)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


async def discover_sitemap_urls(
    origin: str,
    *,
    user_agent: str,
    request_timeout: float = 15.0,
    max_urls: int = 5000,
    max_sitemaps: int = 50,
) -> list[str]:
    """Return the page URLs listed by ``{origin}/sitemap.xml`` (and any it indexes)."""
    found: list[str] = []
    seen: set[str] = set()
    queue: list[str] = [urljoin(origin if origin.endswith("/") else origin + "/", "sitemap.xml")]
    headers = {"User-Agent": user_agent}
    allow_private = get_config().allow_private_hosts
    async with httpx.AsyncClient(
        timeout=request_timeout, headers=headers, follow_redirects=False
    ) as client:
        while queue and len(found) < max_urls and len(seen) < max_sitemaps:
            sm_url = queue.pop(0)
            if sm_url in seen:
                continue
            seen.add(sm_url)
            try:
                # safe_get validates the target (and every redirect hop) against the
                # SSRF guard — important because sitemap-index <loc> entries are
                # attacker-influenceable and could point at internal addresses.
                resp = await safe_get(client, sm_url, allow_private=allow_private)
            except SsrfError as e:
                log.debug("scrapo.sitemap.ssrf_blocked", url=sm_url, err=str(e))
                continue
            except httpx.HTTPError as e:
                log.debug("scrapo.sitemap.fetch_failed", url=sm_url, err=str(e))
                continue
            if resp.status_code != 200:
                continue
            payload = _decode_sitemap(sm_url, resp)
            if payload is None:
                continue
            try:
                root = ET.fromstring(payload)  # noqa: S314 - sitemap from a host we chose to crawl
            except ET.ParseError:
                continue
            is_index = _local(root.tag) == "sitemapindex"
            for loc in root.iter():
                if _local(loc.tag) != "loc":
                    continue
                value = (loc.text or "").strip()
                if not value:
                    continue
                if is_index:
                    queue.append(value)
                else:
                    found.append(value)
                    if len(found) >= max_urls:
                        break
    return found


def _decode_sitemap(url: str, resp: httpx.Response) -> bytes | None:
    """Decompress ``sitemap.xml.gz`` style payloads. httpx already handles a
    ``Content-Encoding: gzip`` *transport* layer; this catches files whose actual
    body is a gzip stream (common on large sites referenced from a sitemap index)."""
    body = resp.content
    looks_gz = url.lower().endswith(".gz") or body[:2] == b"\x1f\x8b"
    if not looks_gz:
        return body
    try:
        return gzip.decompress(body)
    except (OSError, gzip.BadGzipFile):
        log.debug("scrapo.sitemap.bad_gzip", url=url)
        return None
