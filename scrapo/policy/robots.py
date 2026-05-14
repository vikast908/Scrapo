"""robots.txt gate.

Wraps urllib.robotparser with an in-memory cache keyed by origin so a single
crawl never fetches the same robots.txt twice.
"""

from __future__ import annotations

import asyncio
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import httpx
import structlog

log = structlog.get_logger(__name__)


class RobotsGate:
    def __init__(self, user_agent: str, *, timeout: float = 10.0, enabled: bool = True) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.enabled = enabled
        self._cache: dict[str, robotparser.RobotFileParser] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def can_fetch(self, url: str) -> bool:
        if not self.enabled:
            return True
        origin = self._origin(url)
        rp = await self._get_parser(origin)
        if rp is None:
            return True
        try:
            return rp.can_fetch(self.user_agent, url)
        except Exception:
            return True

    async def crawl_delay(self, url: str) -> float | None:
        if not self.enabled:
            return None
        origin = self._origin(url)
        rp = await self._get_parser(origin)
        if rp is None:
            return None
        try:
            delay = rp.crawl_delay(self.user_agent)
            return float(delay) if delay else None
        except Exception:
            return None

    async def _get_parser(self, origin: str) -> robotparser.RobotFileParser | None:
        if origin in self._cache:
            return self._cache[origin]
        lock = self._locks.setdefault(origin, asyncio.Lock())
        async with lock:
            if origin in self._cache:
                return self._cache[origin]
            url = urljoin(origin, "/robots.txt")
            try:
                async with httpx.AsyncClient(
                    timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                    follow_redirects=True,
                ) as client:
                    resp = await client.get(url)
            except httpx.HTTPError as e:
                log.debug("scrapo.robots.fetch_failed", origin=origin, err=str(e))
                self._cache[origin] = _empty()
                return self._cache[origin]
            rp = robotparser.RobotFileParser()
            if resp.status_code == 200 and _looks_like_robots(resp):
                rp.parse(resp.text.splitlines())
            else:
                if resp.status_code == 200:
                    log.info(
                        "scrapo.robots.non_text_response",
                        origin=origin,
                        content_type=resp.headers.get("content-type"),
                    )
                rp.parse([])
            self._cache[origin] = rp
            return rp

    @staticmethod
    def _origin(url: str) -> str:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}"


def _empty() -> robotparser.RobotFileParser:
    rp = robotparser.RobotFileParser()
    rp.parse([])
    return rp


def _looks_like_robots(resp: httpx.Response) -> bool:
    """Skip parsing payloads that aren't really robots.txt — HTML login walls
    served at /robots.txt would otherwise be silently treated as an allow-all
    file, which is the wrong failure mode for a compliance gate."""
    ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if ctype and not (ctype.startswith("text/plain") or ctype == "text/robots"):
        return False
    head = resp.text[:512].lstrip().lower()
    return not head.startswith(("<!doctype", "<html", "<head", "<?xml"))
