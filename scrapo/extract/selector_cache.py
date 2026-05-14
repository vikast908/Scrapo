"""Selector cache, keyed by (host, schema_hash), backed by SQLite.

The hybrid extractor populates this cache the first time the LLM successfully
extracts a schema; subsequent runs hit the cache and avoid LLM calls entirely.

Keyed by the full host (not the registered domain), because ``blog.example.com``
and ``shop.example.com`` are usually built differently and must not share
selectors. A ``failure_count`` is tracked per (host, schema) so the extractor can
evict an entry that keeps failing validation instead of paying the LLM fallback
on every run forever.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scrapo._db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS selectors (
    host TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    field_name TEXT NOT NULL,
    selector TEXT NOT NULL,
    selector_type TEXT NOT NULL DEFAULT 'css',
    extra TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    updated_at REAL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (host, schema_hash, field_name)
);
CREATE INDEX IF NOT EXISTS idx_selectors_lookup ON selectors(host, schema_hash);
"""


def _host(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    # strip userinfo and port
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    if netloc.startswith("["):  # IPv6 literal
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc or url.lower()


class SelectorCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialized = False
        self._init_lock = asyncio.Lock()

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with self._init_lock:
            if self._initialized:
                return
            async with connect(self.db_path) as db:
                await db.executescript(_SCHEMA)
                await db.commit()
            self._initialized = True

    async def get(self, url: str, schema_hash: str) -> dict[str, dict[str, Any]]:
        await self._ensure()
        host = _host(url)
        async with connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT field_name, selector, selector_type, extra "
                "FROM selectors WHERE host=? AND schema_hash=?",
                (host, schema_hash),
            )
            rows = await cur.fetchall()
        out: dict[str, dict[str, Any]] = {}
        for field_name, selector, selector_type, extra in rows:
            out[field_name] = {
                "selector": selector,
                "type": selector_type,
                "extra": json.loads(extra) if extra else {},
            }
        return out

    async def put(
        self,
        url: str,
        schema_hash: str,
        selectors: dict[str, dict[str, Any]],
    ) -> None:
        await self._ensure()
        host = _host(url)
        async with connect(self.db_path) as db:
            for field_name, spec in selectors.items():
                await db.execute(
                    "INSERT INTO selectors(host, schema_hash, field_name, selector, "
                    "selector_type, extra, failure_count) VALUES (?,?,?,?,?,?,0) "
                    "ON CONFLICT(host, schema_hash, field_name) DO UPDATE SET "
                    "selector=excluded.selector, selector_type=excluded.selector_type, "
                    "extra=excluded.extra, failure_count=0, updated_at=strftime('%s','now')",
                    (
                        host,
                        schema_hash,
                        field_name,
                        spec["selector"],
                        spec.get("type", "css"),
                        json.dumps(spec.get("extra", {})) if spec.get("extra") else None,
                    ),
                )
            await db.commit()

    async def record_success(self, url: str, schema_hash: str) -> None:
        await self._ensure()
        host = _host(url)
        async with connect(self.db_path) as db:
            await db.execute(
                "UPDATE selectors SET success_count = success_count + 1, failure_count = 0 "
                "WHERE host=? AND schema_hash=?",
                (host, schema_hash),
            )
            await db.commit()

    async def record_failure(self, url: str, schema_hash: str) -> int:
        """Increment the failure counter; return the highest failure count for the key."""
        await self._ensure()
        host = _host(url)
        async with connect(self.db_path) as db:
            await db.execute(
                "UPDATE selectors SET failure_count = failure_count + 1 "
                "WHERE host=? AND schema_hash=?",
                (host, schema_hash),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT COALESCE(MAX(failure_count), 0) FROM selectors WHERE host=? AND schema_hash=?",
                (host, schema_hash),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def invalidate(self, url: str, schema_hash: str) -> None:
        await self._ensure()
        host = _host(url)
        async with connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM selectors WHERE host=? AND schema_hash=?",
                (host, schema_hash),
            )
            await db.commit()
