"""Persistent watch store — the durable half of the control plane.

A :class:`Watch` (``scrapo.watch``) is in-process: it lives only as long as the
Python object holding its ``last_run_id``. To run a *list* of watches on a
schedule that survives restarts, you need to persist the watch definitions and
their cursors. That's this store: one SQLite table of watch rows, opened through
the same pooled/WAL machinery every other Scrapo store uses.

It deliberately does NOT persist the extraction *schema* (a Pydantic class can't
round-trip through SQLite). A watch registered with a schema keeps it in the
scheduler's in-memory map for that process; a watch loaded fresh from the DB
after a restart tracks page-level changes (HTML / markdown) until it's
re-registered with a schema. See :class:`scrapo.server.scheduler.WatchScheduler`.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

from scrapo._db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    interval_seconds REAL NOT NULL,
    webhook_url TEXT,
    label TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_id TEXT,
    last_checked_at REAL,
    last_changed_at REAL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_watches_due ON watches(enabled, last_checked_at);
"""


@dataclass(slots=True)
class WatchRow:
    """One persisted watch definition + its scheduling cursor."""

    id: str
    url: str
    interval_seconds: float
    webhook_url: str | None
    label: str | None
    enabled: bool
    last_run_id: str | None
    last_checked_at: float | None
    last_changed_at: float | None
    created_at: float

    def is_due(self, now: float) -> bool:
        if not self.enabled:
            return False
        if self.last_checked_at is None:
            return True
        return self.last_checked_at + self.interval_seconds <= now


def _to_row(row: aiosqlite.Row) -> WatchRow:
    return WatchRow(
        id=row["id"],
        url=row["url"],
        interval_seconds=row["interval_seconds"],
        webhook_url=row["webhook_url"],
        label=row["label"],
        enabled=bool(row["enabled"]),
        last_run_id=row["last_run_id"],
        last_checked_at=row["last_checked_at"],
        last_changed_at=row["last_changed_at"],
        created_at=row["created_at"],
    )


class WatchStore:
    """SQLite-backed CRUD for watch definitions (mirrors the other Scrapo stores)."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._lock = asyncio.Lock()
        self._init_lock = asyncio.Lock()
        self._initialized = False

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

    async def add(
        self,
        url: str,
        *,
        interval_seconds: float,
        webhook_url: str | None = None,
        label: str | None = None,
        enabled: bool = True,
    ) -> WatchRow:
        if interval_seconds <= 0:
            raise ValueError(f"interval_seconds must be positive, got {interval_seconds}")
        await self._ensure()
        row = WatchRow(
            id=uuid.uuid4().hex,
            url=url,
            interval_seconds=float(interval_seconds),
            webhook_url=webhook_url,
            label=label,
            enabled=enabled,
            last_run_id=None,
            last_checked_at=None,
            last_changed_at=None,
            created_at=time.time(),
        )
        async with self._lock, connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO watches(id, url, interval_seconds, webhook_url, label, enabled, "
                "last_run_id, last_checked_at, last_changed_at, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    row.id, row.url, row.interval_seconds, row.webhook_url, row.label,
                    int(row.enabled), None, None, None, row.created_at,
                ),
            )
            await db.commit()
        return row

    async def get(self, watch_id: str) -> WatchRow | None:
        await self._ensure()
        async with connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM watches WHERE id=?", (watch_id,))
            row = await cur.fetchone()
            return _to_row(row) if row else None

    async def list_all(self) -> list[WatchRow]:
        await self._ensure()
        async with connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM watches ORDER BY created_at")
            return [_to_row(r) for r in await cur.fetchall()]

    async def due(self, now: float) -> list[WatchRow]:
        await self._ensure()
        async with connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM watches WHERE enabled=1 AND "
                "(last_checked_at IS NULL OR last_checked_at + interval_seconds <= ?) "
                "ORDER BY created_at",
                (now,),
            )
            return [_to_row(r) for r in await cur.fetchall()]

    async def remove(self, watch_id: str) -> bool:
        await self._ensure()
        async with self._lock, connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM watches WHERE id=?", (watch_id,))
            await db.commit()
            return cur.rowcount > 0

    async def set_enabled(self, watch_id: str, enabled: bool) -> None:
        await self._ensure()
        async with self._lock, connect(self.db_path) as db:
            await db.execute(
                "UPDATE watches SET enabled=? WHERE id=?", (int(enabled), watch_id)
            )
            await db.commit()

    async def record_check(
        self,
        watch_id: str,
        *,
        run_id: str | None,
        checked_at: float,
        changed: bool,
    ) -> None:
        """Advance a watch's cursor after a check (and stamp last_changed_at on a change)."""
        await self._ensure()
        async with self._lock, connect(self.db_path) as db:
            if changed:
                await db.execute(
                    "UPDATE watches SET last_run_id=?, last_checked_at=?, last_changed_at=? "
                    "WHERE id=?",
                    (run_id, checked_at, checked_at, watch_id),
                )
            else:
                await db.execute(
                    "UPDATE watches SET last_run_id=?, last_checked_at=? WHERE id=?",
                    (run_id, checked_at, watch_id),
                )
            await db.commit()
