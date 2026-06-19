"""SQLite-backed replay store.

Stores response headers, the typed extraction, and a locator for the page body
of every fetch. Bodies (HTML/JSON gzipped, screenshots raw) go through a pluggable
:class:`~scrapo.replay.snapshots.SnapshotStore` (local files or S3); the locator
string it returns is what the ``runs`` table records.
"""

from __future__ import annotations

import asyncio
import contextlib
import gzip
import json
import time
from dataclasses import asdict
from typing import Any

import aiosqlite

from scrapo._db import connect
from scrapo.config import Config
from scrapo.replay.snapshots import SnapshotStore, from_backend, gunzip, gz
from scrapo.types import ExtractionResult, FetchResult, RunRecord, Tier

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    tier_used INTEGER,
    proxy_region TEXT,
    fetch_status INTEGER,
    extraction_method TEXT,
    model_pinned TEXT,
    schema_version TEXT,
    cost_usd REAL DEFAULT 0,
    llm_calls INTEGER DEFAULT 0,
    error TEXT,
    html_path TEXT,
    headers_json TEXT,
    extraction_json TEXT,
    screenshot_path TEXT,
    etag TEXT,
    last_modified TEXT,
    not_modified INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_runs_url ON runs(url, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_schema ON runs(schema_version);
"""

# columns added after the initial release — applied with ALTER TABLE on existing DBs
_MIGRATIONS: list[tuple[str, str]] = [
    ("etag", "TEXT"),
    ("last_modified", "TEXT"),
    ("not_modified", "INTEGER DEFAULT 0"),
]


class ReplayStore:
    def __init__(self, config: Config, snapshots: SnapshotStore | None = None) -> None:
        self.config = config
        self.db_path = config.replay_db
        self.snapshots = snapshots or from_backend(
            config.snapshot_backend, local_root=config.snapshot_dir
        )
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
                for column, decl in _MIGRATIONS:
                    # column already exists on a fresh DB (in _SCHEMA) or one migrated earlier
                    with contextlib.suppress(aiosqlite.OperationalError):
                        await db.execute(f"ALTER TABLE runs ADD COLUMN {column} {decl}")
                await db.commit()
            self._initialized = True

    async def record(
        self,
        record: RunRecord,
        fetch: FetchResult | None,
        extraction: ExtractionResult | None,
        *,
        html_path: str | None = None,
    ) -> None:
        """Persist a run. Pass ``html_path`` to point at an already-stored snapshot
        (e.g. the prior run's archive on a 304) instead of writing a duplicate."""
        await self._ensure()
        record.finished_at = record.finished_at or time.time()

        screenshot_path: str | None = None
        headers_json: str | None = None

        if fetch is not None:
            blob = fetch.raw_content if fetch.raw_content is not None else (
                fetch.html.encode("utf-8") if fetch.html else None
            )
            if html_path is None and self.config.snapshot_html and blob:
                # gzip compression and the snapshot write (local disk or sync
                # boto3) are blocking; run them off the event loop so concurrent
                # crawl workers are not stalled.
                compressed = await asyncio.to_thread(gz, blob)
                html_path = await asyncio.to_thread(
                    self.snapshots.put, f"{record.run_id}.html.gz", compressed
                )
            if fetch.screenshot_png:
                screenshot_path = await asyncio.to_thread(
                    self.snapshots.put, f"{record.run_id}.png", fetch.screenshot_png
                )
            headers_json = json.dumps(fetch.headers)

        extraction_json: str | None = None
        if extraction is not None:
            extraction_json = json.dumps(_serialize_extraction(extraction), default=str)

        async with connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO runs("
                "run_id, url, started_at, finished_at, tier_used, proxy_region, "
                "fetch_status, extraction_method, model_pinned, schema_version, "
                "cost_usd, llm_calls, error, html_path, headers_json, extraction_json, screenshot_path, "
                "etag, last_modified, not_modified"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.run_id,
                    record.url,
                    record.started_at,
                    record.finished_at,
                    int(record.tier_used) if record.tier_used is not None else None,
                    record.proxy_region,
                    record.fetch_status,
                    record.extraction_method,
                    record.model_pinned,
                    record.schema_version,
                    record.cost_usd,
                    record.llm_calls,
                    record.error,
                    html_path,
                    headers_json,
                    extraction_json,
                    screenshot_path,
                    record.etag,
                    record.last_modified,
                    1 if record.not_modified else 0,
                ),
            )
            await db.commit()

    async def get(self, run_id: str) -> dict[str, Any] | None:
        await self._ensure()
        async with connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,))
            row = await cur.fetchone()
        return dict(row) if row else None

    async def load_html(self, run_id: str) -> str | None:
        rec = await self.get(run_id)
        if not rec or not rec.get("html_path"):
            return None
        raw = await asyncio.to_thread(self.snapshots.get, rec["html_path"])
        if raw is None:
            return None
        # A snapshot can be corrupt (truncated by a crash mid-write, or written
        # by an old version that did not gzip). Treat both as "not available" so
        # the conditional-GET path falls back to a fresh fetch instead of raising.
        try:
            return gunzip(raw).decode("utf-8", errors="replace")
        except (gzip.BadGzipFile, OSError, EOFError):
            return None

    async def list_runs(self, url: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        await self._ensure()
        async with connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if url:
                cur = await db.execute(
                    "SELECT * FROM runs WHERE url=? ORDER BY started_at DESC LIMIT ?",
                    (url, limit),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
                )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def last_run(self, url: str) -> dict[str, Any] | None:
        """The most recent recorded run for ``url`` (any status), or None."""
        rows = await self.list_runs(url=url, limit=1)
        return rows[0] if rows else None


def _serialize_extraction(extraction: ExtractionResult) -> dict[str, Any]:
    out = asdict(extraction)
    data = extraction.data
    if hasattr(data, "model_dump"):
        out["data"] = data.model_dump()
    return out


def _tier_from_int(value: int | None) -> Tier | None:
    if value is None:
        return None
    return Tier(value)
