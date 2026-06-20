"""Change notifiers for the watch scheduler.

When a scheduled watch detects a change, the scheduler hands it to a
:class:`Notifier`. Two ship in:

* :class:`WebhookNotifier` — POSTs a JSON payload to the watch's ``webhook_url``.
* :class:`CallbackNotifier` — calls an in-process function (sync or async).

Implement the :class:`Notifier` protocol for anything else (email, Slack, a queue).
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

import structlog

if TYPE_CHECKING:
    from scrapo.server.scheduler import CheckOutcome
    from scrapo.server.store import WatchRow

log = structlog.get_logger(__name__)


class Notifier(Protocol):
    async def notify(self, watch: WatchRow, outcome: CheckOutcome) -> None: ...


def _payload(watch: WatchRow, outcome: CheckOutcome) -> dict[str, Any]:
    return {
        "watch_id": outcome.watch_id,
        "url": outcome.url,
        "label": watch.label,
        "changed": outcome.changed,
        "not_modified": outcome.not_modified,
        "run_id": outcome.run_id,
        "summary": outcome.summary,
        "field_changes": outcome.field_changes,
    }


class WebhookNotifier:
    """POST a JSON change payload to each watch's configured ``webhook_url``."""

    def __init__(self, *, timeout: float = 10.0) -> None:
        self.timeout = timeout

    async def notify(self, watch: WatchRow, outcome: CheckOutcome) -> None:
        if not watch.webhook_url:
            return
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            await client.post(watch.webhook_url, json=_payload(watch, outcome))


class CallbackNotifier:
    """Invoke an in-process callback on each change (sync or async)."""

    def __init__(
        self,
        callback: Callable[[WatchRow, CheckOutcome], Awaitable[None] | None],
    ) -> None:
        self._callback = callback

    async def notify(self, watch: WatchRow, outcome: CheckOutcome) -> None:
        result = self._callback(watch, outcome)
        if inspect.isawaitable(result):
            await result
