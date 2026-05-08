"""Append-only audit log — JSON Lines on disk."""

from __future__ import annotations

import asyncio
import json
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
        line = json.dumps(payload, default=str, separators=(",", ":")) + "\n"
        async with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    async def tail(self, n: int = 50) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self.path.open("r", encoding="utf-8") as fh:
            lines = fh.readlines()
        out: list[dict[str, Any]] = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
