"""Selector cache, keyed by (host, path_template, schema_hash), backed by SQLite.

The hybrid extractor populates this cache the first time the LLM successfully
extracts a schema; subsequent runs hit the cache and avoid LLM calls entirely.

Keyed by the full host (not the registered domain), because ``blog.example.com``
and ``shop.example.com`` are usually built differently and must not share
selectors. A coarse PATH-TEMPLATE token derived from the URL path is also part
of the key, so different page templates on the same host (e.g. ``/product/X``
vs ``/category/Y``) don't thrash a single shared entry — selectors learned on a
product page won't get evicted by failures on a category page and vice versa.
Pages of the *same* template (``/product/12345/foo`` and ``/product/67890/bar``)
collapse to the same template token and share a key.

A ``failure_count`` is tracked per (host, path_template, schema) so the extractor
can evict an entry that keeps failing validation instead of paying the LLM
fallback on every run forever.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from scrapo._db import connect

# Table first; the index (which references path_template) is created only AFTER
# _migrate() has ensured the column exists — see _ensure().
_SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS selectors (
    host TEXT NOT NULL,
    path_template TEXT NOT NULL DEFAULT '',
    schema_hash TEXT NOT NULL,
    field_name TEXT NOT NULL,
    selector TEXT NOT NULL,
    selector_type TEXT NOT NULL DEFAULT 'css',
    extra TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    updated_at REAL DEFAULT (strftime('%s','now')),
    PRIMARY KEY (host, path_template, schema_hash, field_name)
);
"""

_SCHEMA_INDEX = """
CREATE INDEX IF NOT EXISTS idx_selectors_lookup
    ON selectors(host, path_template, schema_hash);
"""

# A path segment is "id-like" (replaced by a placeholder) when it is numeric,
# a hex/UUID-ish token, or a long slug — i.e. an instance identifier rather than
# a structural template segment.
_ID_SEGMENT = re.compile(
    r"""^(
        \d+                                  # pure numeric id
        | [0-9a-fA-F]{8,}                    # hex / uuid-ish token
        | .*\d.*-.*                          # slug containing a digit and a dash
        | [^/]{40,}                          # very long opaque segment
    )$""",
    re.VERBOSE,
)


def _host(url: str) -> str:
    netloc = urlsplit(url).netloc.lower()
    # strip userinfo and port
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]
    if netloc.startswith("["):  # IPv6 literal
        return netloc.split("]", 1)[0] + "]"
    return netloc.rsplit(":", 1)[0] if ":" in netloc else netloc or url.lower()


def _path_template(url: str) -> str:
    """Derive a stable, coarse template token from a URL path.

    Takes the first 1-2 path segments and replaces numeric / id-like / slug
    segments with ``:id`` so pages of the same template share a token:

        /product/12345/foo  -> product/:id
        /product/67890/bar  -> product/:id
        /category/widgets   -> category/widgets

    The empty path (site root) yields ``""``.
    """
    path = urlsplit(url).path
    segments = [s for s in path.split("/") if s]
    out: list[str] = []
    for seg in segments[:2]:
        out.append(":id" if _ID_SEGMENT.match(seg) else seg.lower())
    return "/".join(out)


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
                await self._migrate(db)
                # Create the table for fresh DBs (no-op once _migrate has rebuilt
                # an old one), then the index that references path_template.
                await db.executescript(_SCHEMA_TABLE)
                await db.executescript(_SCHEMA_INDEX)
                await db.commit()
            self._initialized = True

    @staticmethod
    async def _migrate(db: Any) -> None:
        """Bring a pre-existing (old-schema) DB up to the path_template keying.

        Fresh DBs are created with the new schema by CREATE TABLE. This handles
        databases created before path-template keying existed: the old PRIMARY
        KEY was ``(host, schema_hash, field_name)`` with no ``path_template``
        column, so a plain ``ALTER TABLE ADD COLUMN`` isn't enough — the
        ON CONFLICT clause needs ``path_template`` in the key. We detect the old
        table (no ``path_template`` column) and rebuild it: create the new-shape
        table, copy existing rows in under the ``''`` template, and swap. This is
        alpha software, so a one-time rebuild is acceptable.
        """
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='selectors'"
        )
        if await cur.fetchone() is None:
            return  # fresh DB — nothing to migrate
        cur = await db.execute("PRAGMA table_info(selectors)")
        cols = {row[1] for row in await cur.fetchall()}
        if "path_template" in cols:
            return  # already migrated
        # Rebuild with the new schema and PRIMARY KEY, copying old rows in.
        await db.executescript(
            _SCHEMA_TABLE.replace("selectors", "selectors_new")
        )
        await db.execute(
            "INSERT INTO selectors_new "
            "(host, path_template, schema_hash, field_name, selector, "
            " selector_type, extra, success_count, failure_count, updated_at) "
            "SELECT host, '', schema_hash, field_name, selector, "
            " selector_type, extra, success_count, failure_count, updated_at "
            "FROM selectors"
        )
        await db.execute("DROP TABLE selectors")
        await db.execute("ALTER TABLE selectors_new RENAME TO selectors")

    async def get(self, url: str, schema_hash: str) -> dict[str, dict[str, Any]]:
        await self._ensure()
        host = _host(url)
        tmpl = _path_template(url)
        async with connect(self.db_path) as db:
            cur = await db.execute(
                "SELECT field_name, selector, selector_type, extra "
                "FROM selectors WHERE host=? AND path_template=? AND schema_hash=?",
                (host, tmpl, schema_hash),
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
        tmpl = _path_template(url)
        async with connect(self.db_path) as db:
            for field_name, spec in selectors.items():
                await db.execute(
                    "INSERT INTO selectors(host, path_template, schema_hash, field_name, "
                    "selector, selector_type, extra, failure_count) VALUES (?,?,?,?,?,?,?,0) "
                    "ON CONFLICT(host, path_template, schema_hash, field_name) DO UPDATE SET "
                    "selector=excluded.selector, selector_type=excluded.selector_type, "
                    "extra=excluded.extra, failure_count=0, updated_at=strftime('%s','now')",
                    (
                        host,
                        tmpl,
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
        tmpl = _path_template(url)
        async with connect(self.db_path) as db:
            await db.execute(
                "UPDATE selectors SET success_count = success_count + 1, failure_count = 0 "
                "WHERE host=? AND path_template=? AND schema_hash=?",
                (host, tmpl, schema_hash),
            )
            await db.commit()

    async def record_failure(self, url: str, schema_hash: str) -> int:
        """Increment the failure counter; return the highest failure count for the key."""
        await self._ensure()
        host = _host(url)
        tmpl = _path_template(url)
        async with connect(self.db_path) as db:
            await db.execute(
                "UPDATE selectors SET failure_count = failure_count + 1 "
                "WHERE host=? AND path_template=? AND schema_hash=?",
                (host, tmpl, schema_hash),
            )
            await db.commit()
            cur = await db.execute(
                "SELECT COALESCE(MAX(failure_count), 0) FROM selectors "
                "WHERE host=? AND path_template=? AND schema_hash=?",
                (host, tmpl, schema_hash),
            )
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def invalidate(self, url: str, schema_hash: str) -> None:
        await self._ensure()
        host = _host(url)
        tmpl = _path_template(url)
        async with connect(self.db_path) as db:
            await db.execute(
                "DELETE FROM selectors WHERE host=? AND path_template=? AND schema_hash=?",
                (host, tmpl, schema_hash),
            )
            await db.commit()
