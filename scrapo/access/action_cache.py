"""Agent action cache, keyed by (host, goal_hash), backed by SQLite.

Tier 4 (LLM-driven browsing) normally pays one LLM call per step. The first time
the agent driver reaches a goal on a host it records the ordered list of actions
it took; later runs with the same goal replay that list directly — zero LLM
tokens — and only fall back to the model if a replayed action no longer applies
(its element is gone, a navigation fails, …). A ``failure_count`` per
(host, goal) lets the driver evict a recording that keeps breaking instead of
retrying a stale script forever.

This is the "Stagehand-style action caching" piece: the agent driver has no
goal-verification signal of its own, so a recording is invalidated when a
replayed *action* fails to execute, not when the goal turns out unmet.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scrapo._db import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_actions (
    host TEXT NOT NULL,
    goal_hash TEXT NOT NULL,
    goal TEXT NOT NULL,
    actions_json TEXT NOT NULL,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    updated_at REAL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (host, goal_hash)
);
"""


def _host(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    if netloc.startswith("["):  # IPv6 literal
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc or url.lower()


def _goal_hash(goal: str) -> str:
    return hashlib.sha256(goal.strip().lower().encode("utf-8")).hexdigest()[:16]


class ActionCache:
    """Ordered agent action sequences, keyed by (host, goal)."""

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

    async def get(self, url: str, goal: str) -> list[dict[str, Any]]:
        await self._ensure()
        async with connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT actions_json FROM agent_actions WHERE host=? AND goal_hash=?",
                (_host(url), _goal_hash(goal)),
            )
            row = await cur.fetchone()
        if not row:
            return []
        try:
            actions = json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return []
        return [a for a in actions if isinstance(a, dict)] if isinstance(actions, list) else []

    async def put(self, url: str, goal: str, actions: list[dict[str, Any]]) -> None:
        await self._ensure()
        async with connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO agent_actions(host, goal_hash, goal, actions_json, failure_count) "
                "VALUES (?,?,?,?,0) "
                "ON CONFLICT(host, goal_hash) DO UPDATE SET "
                "actions_json=excluded.actions_json, goal=excluded.goal, "
                "failure_count=0, updated_at=strftime('%s','now')",
                (_host(url), _goal_hash(goal), goal.strip(), json.dumps(actions)),
            )
            await db.commit()

    async def record_success(self, url: str, goal: str) -> None:
        await self._ensure()
        async with connect(self.db_path) as db:
            await db.execute(
                "UPDATE agent_actions SET success_count = success_count + 1, failure_count = 0 "
                "WHERE host=? AND goal_hash=?",
                (_host(url), _goal_hash(goal)),
            )
            await db.commit()

    async def record_failure(self, url: str, goal: str) -> int:
        """Increment the failure counter; return the current failure count for the key."""
        await self._ensure()
        async with connect(self.db_path) as db:
            await db.execute(
                "UPDATE agent_actions SET failure_count = failure_count + 1 "
                "WHERE host=? AND goal_hash=?",
                (_host(url), _goal_hash(goal)),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT COALESCE(failure_count, 0) FROM agent_actions WHERE host=? AND goal_hash=?",
                (_host(url), _goal_hash(goal)),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def invalidate(self, url: str, goal: str) -> None:
        await self._ensure()
        async with connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM agent_actions WHERE host=? AND goal_hash=?",
                (_host(url), _goal_hash(goal)),
            )
            await db.commit()
