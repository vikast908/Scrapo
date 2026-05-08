"""Persistent SQLite-backed request queue.

Survives process restarts, supports per-host rate-limit keys, and exposes a
simple {claim, complete, fail} API.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_id TEXT NOT NULL,
    url TEXT NOT NULL,
    depth INTEGER NOT NULL DEFAULT 0,
    parent_url TEXT,
    metadata TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at REAL NOT NULL,
    claimed_at REAL,
    finished_at REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_q_pending ON requests(crawl_id, status, id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_q_url ON requests(crawl_id, url);
"""


class RequestQueue:
    def __init__(self, db_path: Path, crawl_id: str) -> None:
        self.db_path = db_path
        self.crawl_id = crawl_id
        self._lock = asyncio.Lock()
        self._initialized = False

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def enqueue(
        self,
        url: str,
        depth: int = 0,
        parent_url: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        await self._ensure()
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "INSERT INTO requests(crawl_id, url, depth, parent_url, metadata, enqueued_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        self.crawl_id,
                        url,
                        depth,
                        parent_url,
                        json.dumps(metadata) if metadata else None,
                        time.time(),
                    ),
                )
                await db.commit()
                return True
            except aiosqlite.IntegrityError:
                return False

    async def claim(self) -> dict[str, Any] | None:
        await self._ensure()
        async with self._lock, aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM requests WHERE crawl_id=? AND status='pending' "
                "ORDER BY id LIMIT 1",
                (self.crawl_id,),
            )
            row = await cur.fetchone()
            if not row:
                return None
            await db.execute(
                "UPDATE requests SET status='in_flight', claimed_at=?, attempts=attempts+1 "
                "WHERE id=?",
                (time.time(), row["id"]),
            )
            await db.commit()
            return dict(row)

    async def complete(self, request_id: int) -> None:
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE requests SET status='done', finished_at=? WHERE id=?",
                (time.time(), request_id),
            )
            await db.commit()

    async def fail(self, request_id: int, error: str, retry: bool = True) -> None:
        await self._ensure()
        new_status = "pending" if retry else "failed"
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE requests SET status=?, error=?, claimed_at=NULL WHERE id=?",
                (new_status, error[:500], request_id),
            )
            await db.commit()

    async def stats(self) -> dict[str, int]:
        await self._ensure()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT status, COUNT(*) FROM requests WHERE crawl_id=? GROUP BY status",
                (self.crawl_id,),
            )
            return {row[0]: row[1] for row in await cur.fetchall()}
