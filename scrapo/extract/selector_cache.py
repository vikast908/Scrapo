"""Selector cache — keyed by (domain, schema_hash), backed by SQLite.

The hybrid extractor populates this cache the first time the LLM successfully
extracts a schema; subsequent runs hit the cache and avoid LLM calls entirely.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import aiosqlite
import tldextract


_SCHEMA = """
CREATE TABLE IF NOT EXISTS selectors (
    domain TEXT NOT NULL,
    schema_hash TEXT NOT NULL,
    field_name TEXT NOT NULL,
    selector TEXT NOT NULL,
    selector_type TEXT NOT NULL DEFAULT 'css',
    extra TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    updated_at REAL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (domain, schema_hash, field_name)
);
CREATE INDEX IF NOT EXISTS idx_selectors_lookup ON selectors(domain, schema_hash);
"""


def _domain(url: str) -> str:
    parts = tldextract.extract(url)
    if parts.registered_domain:
        return parts.registered_domain.lower()
    return parts.domain.lower() if parts.domain else url.lower()


class SelectorCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._initialized = False

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def get(self, url: str, schema_hash: str) -> dict[str, dict[str, Any]]:
        await self._ensure()
        domain = _domain(url)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT field_name, selector, selector_type, extra "
                "FROM selectors WHERE domain=? AND schema_hash=?",
                (domain, schema_hash),
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
        domain = _domain(url)
        async with aiosqlite.connect(self.db_path) as db:
            for field_name, spec in selectors.items():
                await db.execute(
                    "INSERT INTO selectors(domain, schema_hash, field_name, selector, selector_type, extra) "
                    "VALUES (?,?,?,?,?,?) "
                    "ON CONFLICT(domain, schema_hash, field_name) DO UPDATE SET "
                    "selector=excluded.selector, selector_type=excluded.selector_type, "
                    "extra=excluded.extra, updated_at=strftime('%s','now')",
                    (
                        domain,
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
        domain = _domain(url)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE selectors SET success_count = success_count + 1 "
                "WHERE domain=? AND schema_hash=?",
                (domain, schema_hash),
            )
            await db.commit()

    async def record_failure(self, url: str, schema_hash: str) -> None:
        await self._ensure()
        domain = _domain(url)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE selectors SET failure_count = failure_count + 1 "
                "WHERE domain=? AND schema_hash=?",
                (domain, schema_hash),
            )
            await db.commit()

    async def invalidate(self, url: str, schema_hash: str) -> None:
        await self._ensure()
        domain = _domain(url)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM selectors WHERE domain=? AND schema_hash=?",
                (domain, schema_hash),
            )
            await db.commit()
