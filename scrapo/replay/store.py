"""SQLite-backed replay store.

Stores raw HTML, response headers, optional screenshot, and the typed
extraction output for every fetch. The HTML lives on disk under
{snapshot_dir}/{run_id}.html.gz; the SQLite row tracks metadata + paths.
"""

from __future__ import annotations

import gzip
import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import aiosqlite

from scrapo._db import connect
from scrapo.config import Config
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
    screenshot_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_url ON runs(url, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_schema ON runs(schema_version);
"""


class ReplayStore:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.db_path = config.replay_db
        self.snapshot_dir = config.snapshot_dir
        self._initialized = False

    async def _ensure(self) -> None:
        if self._initialized:
            return
        async with connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()
        self._initialized = True

    async def record(
        self,
        record: RunRecord,
        fetch: FetchResult | None,
        extraction: ExtractionResult | None,
    ) -> None:
        await self._ensure()
        record.finished_at = record.finished_at or time.time()

        html_path: Path | None = None
        screenshot_path: Path | None = None
        headers_json: str | None = None

        if fetch is not None:
            if self.config.snapshot_html and fetch.html:
                html_path = self.snapshot_dir / f"{record.run_id}.html.gz"
                html_path.write_bytes(gzip.compress(fetch.html.encode("utf-8"), compresslevel=6))
            if fetch.screenshot_png:
                screenshot_path = self.snapshot_dir / f"{record.run_id}.png"
                screenshot_path.write_bytes(fetch.screenshot_png)
            headers_json = json.dumps(fetch.headers)

        extraction_json: str | None = None
        if extraction is not None:
            extraction_json = json.dumps(_serialize_extraction(extraction), default=str)

        async with connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO runs("
                "run_id, url, started_at, finished_at, tier_used, proxy_region, "
                "fetch_status, extraction_method, model_pinned, schema_version, "
                "cost_usd, llm_calls, error, html_path, headers_json, extraction_json, screenshot_path"
                ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                    str(html_path) if html_path else None,
                    headers_json,
                    extraction_json,
                    str(screenshot_path) if screenshot_path else None,
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
        path = Path(rec["html_path"])
        if not path.exists():
            return None
        return gzip.decompress(path.read_bytes()).decode("utf-8", errors="replace")

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
