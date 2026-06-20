"""Self-hosted watch control plane.

The library half of a "tell me when this page changes" service: persist a list of
watches, run them on a schedule that survives restarts, and fire a webhook (or an
in-process callback) when a watch detects a change.

    import asyncio
    from scrapo.server import WatchStore, WatchScheduler, WebhookNotifier

    async def main():
        store = WatchStore(Config().watch_db)
        sched = WatchScheduler(store, notifier=WebhookNotifier())
        await sched.add("https://example.com/pricing", interval_seconds=3600,
                        webhook_url="https://hooks.example/scrapo")
        await sched.run_forever()

    asyncio.run(main())

A full multi-tenant web console with auth is a separate deployable app built on
top of this engine, not part of the library.
"""

from scrapo.server.notifier import CallbackNotifier, Notifier, WebhookNotifier
from scrapo.server.scheduler import CheckOutcome, WatchScheduler
from scrapo.server.store import WatchRow, WatchStore

__all__ = [
    "CallbackNotifier",
    "CheckOutcome",
    "Notifier",
    "WatchRow",
    "WatchScheduler",
    "WatchStore",
    "WebhookNotifier",
]
