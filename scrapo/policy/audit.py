"""Append-only audit log — JSON Lines on disk."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any


class AuditLog:
    def __init__(self, path: Path, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = asyncio.Lock()

    async def record(self, event: str, **fields: Any) -> None:
        if not self.enabled:
            return
        payload = {"ts": time.time(), "event": event, **fields}
        # Append in a single OS write so concurrent writers (separate processes
        # sharing the same log path) don't interleave JSON lines. On POSIX local
        # filesystems, O_APPEND writes under PIPE_BUF are atomic and JSONL records
        # here are well under that limit. This is best-effort only on non-POSIX
        # platforms (Windows) and on network filesystems (NFS), where O_APPEND
        # atomicity is not guaranteed.
        data = (json.dumps(payload, default=str, separators=(",", ":")) + "\n").encode("utf-8")
        async with self._lock:
            # The open/write/close is blocking IO; run it off the event loop so a
            # large or slow log device can't stall concurrent crawl workers. The
            # single O_APPEND os.write preserves the atomicity guarantee above.
            await asyncio.to_thread(self._append, data)

    def _append(self, data: bytes) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)

    async def tail(self, n: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        # A large append-only log can be many MB; reading it blocks the loop.
        lines = await asyncio.to_thread(self._read_lines)
        out: list[dict[str, Any]] = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _read_lines(self) -> list[str]:
        with self.path.open("r", encoding="utf-8") as fh:
            return fh.readlines()
