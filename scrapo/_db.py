"""Shared SQLite connection helper.

Every store in scrapo (replay, selector cache, crawl queue) opens short-lived
aiosqlite connections. Routing them through one helper lets us apply the same
durability and concurrency pragmas everywhere: WAL so readers never block the
writer, and a busy timeout so concurrent writers retry instead of failing with
"database is locked".
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

BUSY_TIMEOUT_MS = 5000


@asynccontextmanager
async def connect(db_path: Path | str) -> AsyncIterator[aiosqlite.Connection]:
    """Open a tuned aiosqlite connection as an async context manager."""
    db = await aiosqlite.connect(db_path)
    try:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        await db.execute("PRAGMA foreign_keys=ON")
        yield db
    finally:
        await db.close()
