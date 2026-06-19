"""URL and chunk dedup helpers."""

from __future__ import annotations

import asyncio
import hashlib
from urllib.parse import urldefrag, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_", "fbclid", "gclid", "mc_eid", "mc_cid", "ref")


def normalize_url(url: str) -> str:
    """Drop fragment + tracking params, lowercase scheme/host, sort query keys."""
    url, _frag = urldefrag(url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (
        scheme == "https" and netloc.endswith(":443")
    ):
        netloc = netloc.rsplit(":", 1)[0]
    query_pairs = []
    for piece in parts.query.split("&"):
        if not piece:
            continue
        key = piece.split("=", 1)[0]
        if any(key.lower().startswith(p) for p in _TRACKING_PREFIXES):
            continue
        query_pairs.append(piece)
    query_pairs.sort()
    return urlunsplit((scheme, netloc, parts.path or "/", "&".join(query_pairs), ""))


class UrlDeduper:
    def __init__(self) -> None:
        self._seen: set[str] = set()
        # Guards the check-then-set on ``_seen`` so concurrent crawl workers
        # can't both observe the same URL as unseen and both enqueue it.
        self._lock = asyncio.Lock()

    async def add(self, url: str) -> bool:
        """Atomically mark ``url`` as seen; return True only for the first caller."""
        norm = normalize_url(url)
        async with self._lock:
            if norm in self._seen:
                return False
            self._seen.add(norm)
            return True

    def __contains__(self, url: str) -> bool:
        return normalize_url(url) in self._seen


def chunk_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
