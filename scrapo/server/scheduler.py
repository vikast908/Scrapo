"""Watch scheduler — the running half of the control plane.

Wraps a :class:`~scrapo.server.store.WatchStore` with a loop that, on each tick,
finds the watches that are due, re-checks each one (re-scrape + field diff via the
existing :class:`scrapo.watch.Watch`), advances its cursor in the store, and fires
a :class:`~scrapo.server.notifier.Notifier` when something changed.

The check function and the clock are injectable so the whole thing is testable
offline — no network, no real time. The default check uses :class:`scrapo.watch.Watch`,
which benefits from conditional GET (an unchanged page costs ~nothing).

This is the library engine for a self-hosted control plane. A multi-tenant web
console with auth is a separate deployable app built *on top of* this — out of
scope for the library.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from pydantic import BaseModel

from scrapo.config import Config, get_config
from scrapo.server.notifier import Notifier
from scrapo.server.store import WatchRow, WatchStore
from scrapo.watch import Watch

log = structlog.get_logger(__name__)

CheckFn = Callable[[WatchRow], Awaitable["CheckOutcome"]]


@dataclass(slots=True)
class CheckOutcome:
    """The result of checking one watch on a tick."""

    watch_id: str
    url: str
    changed: bool
    not_modified: bool
    run_id: str | None
    summary: str
    field_changes: list[str] = field(default_factory=list)


class WatchScheduler:
    """Runs persisted watches on their intervals and notifies on change."""

    def __init__(
        self,
        store: WatchStore,
        *,
        config: Config | None = None,
        notifier: Notifier | None = None,
        check_fn: CheckFn | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.config = config or get_config()
        self.notifier = notifier
        self._check_fn = check_fn
        self._clock = clock
        # Per-process schema registry: schemas can't be persisted (they're classes),
        # so a watch added with a schema in THIS process gets typed extraction; a
        # watch loaded from disk without one tracks page-level changes.
        self._schemas: dict[str, type[BaseModel]] = {}

    async def add(
        self,
        url: str,
        *,
        interval_seconds: float,
        webhook_url: str | None = None,
        label: str | None = None,
        schema: type[BaseModel] | None = None,
        enabled: bool = True,
    ) -> WatchRow:
        """Register and persist a watch (optionally with a typed schema for this process)."""
        row = await self.store.add(
            url,
            interval_seconds=interval_seconds,
            webhook_url=webhook_url,
            label=label,
            enabled=enabled,
        )
        if schema is not None:
            self._schemas[row.id] = schema
        return row

    async def _default_check(self, row: WatchRow) -> CheckOutcome:
        schema = self._schemas.get(row.id)
        watch = Watch(
            url=row.url, schema=schema, config=self.config, last_run_id=row.last_run_id
        )
        change = await watch.refresh()
        return CheckOutcome(
            watch_id=row.id,
            url=row.url,
            changed=change.changed,
            not_modified=change.not_modified,
            run_id=change.result.run_id,
            summary=change.summary(),
            field_changes=[str(d) for d in change.field_changes],
        )

    async def _check(self, row: WatchRow) -> CheckOutcome:
        if self._check_fn is not None:
            return await self._check_fn(row)
        return await self._default_check(row)

    async def tick(self, now: float | None = None) -> list[CheckOutcome]:
        """Check every due watch once; advance cursors and notify on change."""
        now = now if now is not None else self._clock()
        due = await self.store.due(now)
        outcomes: list[CheckOutcome] = []
        for row in due:
            try:
                outcome = await self._check(row)
            except Exception as exc:  # noqa: BLE001 - one bad watch must not stall the rest
                log.warning("scrapo.watch.check_failed", id=row.id, url=row.url, err=str(exc))
                # Still advance the cursor so a persistently-failing watch doesn't
                # busy-loop (it would be due again on the very next tick otherwise).
                await self.store.record_check(
                    row.id, run_id=row.last_run_id, checked_at=now, changed=False
                )
                continue
            await self.store.record_check(
                row.id, run_id=outcome.run_id, checked_at=now, changed=outcome.changed
            )
            outcomes.append(outcome)
            if outcome.changed and self.notifier is not None:
                try:
                    await self.notifier.notify(row, outcome)
                except Exception as exc:  # noqa: BLE001 - a failed notify can't abort the tick
                    log.warning("scrapo.watch.notify_failed", id=row.id, err=str(exc))
        return outcomes

    async def run_forever(
        self,
        *,
        poll_seconds: float | None = None,
        stop: asyncio.Event | None = None,
    ) -> None:
        """Tick, sleep, repeat until ``stop`` is set (or forever).

        ``poll_seconds`` is how often the loop wakes to look for due watches, not
        the watch intervals themselves (those are per-watch). When a ``stop`` event
        is supplied the sleep is interruptible, so shutdown is prompt.
        """
        poll = poll_seconds if poll_seconds is not None else self.config.watch_poll_seconds
        while True:
            if stop is not None and stop.is_set():
                return
            await self.tick()
            if stop is None:
                await asyncio.sleep(poll)
                continue
            try:
                await asyncio.wait_for(stop.wait(), timeout=poll)
            except TimeoutError:
                continue
            return  # stop was set during the wait
