"""Shared SQLite connection pool.

Every store in scrapo (replay, selector cache, crawl queue, action cache) opens
aiosqlite connections through this helper. Routing them through one place lets us
apply the same durability and concurrency pragmas everywhere: WAL so readers
never block the writer, and a busy timeout so concurrent writers retry instead
of failing with "database is locked".

Connections are *pooled* per database path rather than opened and closed on
every operation. Each ``aiosqlite.connect`` spawns a background OS thread and a
fresh connection has to re-run the PRAGMAs, so opening one per get/put across a
multi-page crawl is thousands of wasted thread-spawns and round-trips. WAL is a
persistent property of the database file, so it only needs to be set once per
physical connection.

Design — a small per-path pool (not a single shared connection):

* ``async with connect(path) as db:`` checks out an *idle* connection from the
  pool for ``path`` and checks it back in on exit. Two concurrent coroutines
  therefore get two *different* connections, which avoids the transaction
  interleaving hazard where one coroutine's ``commit()`` would flush another
  coroutine's half-written statements (the failure mode of sharing a single
  connection).
* The pool is created on demand and capped at :data:`MAX_PER_PATH` connections
  per path. New connections are created lazily up to the cap; once the cap is
  reached a checkout waits for an in-use connection to be returned.
* PRAGMAs are applied exactly once, when a physical connection is first created.
* On check-in, any open transaction is rolled back and per-connection state that
  callers mutate (``row_factory``) is reset, so a reused connection never leaks
  uncommitted writes or a stale row factory to the next caller.

Re-entrancy: the existing stores never nest two ``connect()`` blocks for the
same path — each ``_ensure()`` completes its own block before the caller opens
one — so the cap cannot self-deadlock. If a future caller *does* nest same-path
blocks it must keep the cap above the nesting depth.

Pooled connections live for the lifetime of the process; this is intentional.
:func:`close_all` is provided for clean teardown in tests. It is not registered
with ``atexit`` because that hook cannot drive async close; leaking the
connections to process exit is acceptable.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

BUSY_TIMEOUT_MS = 5000
MAX_PER_PATH = 5


class _Pool:
    """A capped pool of tuned aiosqlite connections for one database path."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._idle: deque[aiosqlite.Connection] = deque()
        self._total = 0  # connections created (idle + checked out)
        # Guards _idle/_total mutation and signals waiters when a connection
        # becomes available (returned to _idle, or capacity frees up).
        self._cond = asyncio.Condition()

    async def _new_connection(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self._db_path)
        try:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA synchronous=NORMAL")
            await db.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            await db.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            await db.close()
            raise
        return db

    async def acquire(self) -> aiosqlite.Connection:
        # Decide whether to reuse an idle connection, create a new one, or wait.
        # Connection creation happens outside the lock (it awaits I/O and a
        # thread spawn) but the slot is reserved under the lock first.
        while True:
            async with self._cond:
                if self._idle:
                    return self._idle.popleft()
                if self._total < MAX_PER_PATH:
                    self._total += 1
                    break  # reserved a slot; create below
                # At cap with nothing idle: wait for a release/return.
                await self._cond.wait()
        try:
            return await self._new_connection()
        except BaseException:
            # Creation failed; give the reserved slot back so we don't leak cap.
            async with self._cond:
                self._total -= 1
                self._cond.notify()
            raise

    async def release(self, db: aiosqlite.Connection) -> None:
        # Reset connection state so the next checkout starts clean.
        healthy = True
        try:
            await db.rollback()  # discard any transaction the caller left open
        except Exception:
            healthy = False
        db.row_factory = None

        async with self._cond:
            if healthy:
                self._idle.append(db)
            else:
                self._total -= 1
            self._cond.notify()
        if not healthy:
            with contextlib.suppress(Exception):
                await db.close()

    async def close_all(self) -> None:
        async with self._cond:
            idle = list(self._idle)
            self._idle.clear()
            self._total -= len(idle)
        for db in idle:
            with contextlib.suppress(Exception):
                await db.close()


_pools: dict[str, _Pool] = {}
_pools_lock = asyncio.Lock()


def _key(db_path: Path | str) -> str:
    """Canonical pool key so ``Path("x")`` and ``"x"`` map to one pool."""
    # abspath (not resolve) avoids touching the filesystem / following symlinks,
    # while still normalizing relative paths and separators.
    return os.path.normcase(os.path.abspath(os.fspath(db_path)))


async def _get_pool(db_path: Path | str) -> _Pool:
    key = _key(db_path)
    pool = _pools.get(key)
    if pool is not None:
        return pool
    async with _pools_lock:  # guard against concurrent first-time creation
        pool = _pools.get(key)
        if pool is None:
            pool = _Pool(key)
            _pools[key] = pool
        return pool


@asynccontextmanager
async def connect(db_path: Path | str) -> AsyncIterator[aiosqlite.Connection]:
    """Check out a tuned aiosqlite connection from the per-path pool.

    Yields a usable :class:`aiosqlite.Connection` for the duration of the
    ``async with`` block and returns it to the pool on exit. PRAGMAs are applied
    once per physical connection, not per checkout.
    """
    pool = await _get_pool(db_path)
    db = await pool.acquire()
    try:
        yield db
    finally:
        await pool.release(db)


async def close_all() -> None:
    """Close every pooled connection across all paths (for teardown / tests)."""
    async with _pools_lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        await pool.close_all()
