"""Watch control plane: store CRUD, scheduler ticks, notifier dispatch."""

import pytest

from scrapo.config import Config
from scrapo.server import (
    CallbackNotifier,
    CheckOutcome,
    WatchScheduler,
    WatchStore,
)
from scrapo.server.store import WatchRow


@pytest.fixture
def store(tmp_path):
    return WatchStore(tmp_path / "watches.sqlite")


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "scrapo")


# --- store -----------------------------------------------------------------

async def test_add_and_get(store):
    row = await store.add("https://a/", interval_seconds=60, label="alpha")
    assert row.url == "https://a/"
    fetched = await store.get(row.id)
    assert fetched is not None
    assert fetched.label == "alpha"
    assert fetched.enabled is True
    assert fetched.last_checked_at is None


async def test_add_rejects_nonpositive_interval(store):
    with pytest.raises(ValueError, match="interval_seconds must be positive"):
        await store.add("https://a/", interval_seconds=0)


async def test_list_and_remove(store):
    a = await store.add("https://a/", interval_seconds=60)
    await store.add("https://b/", interval_seconds=60)
    assert len(await store.list_all()) == 2
    assert await store.remove(a.id) is True
    assert await store.remove(a.id) is False  # already gone
    remaining = await store.list_all()
    assert [r.url for r in remaining] == ["https://b/"]


async def test_due_selection_respects_interval(store):
    row = await store.add("https://a/", interval_seconds=100)
    # Never checked → due immediately.
    assert [r.id for r in await store.due(now=1000.0)] == [row.id]
    # Checked at t=1000; not due until t=1100.
    await store.record_check(row.id, run_id="run1", checked_at=1000.0, changed=False)
    assert await store.due(now=1050.0) == []
    assert [r.id for r in await store.due(now=1100.0)] == [row.id]


async def test_disabled_watch_not_due(store):
    row = await store.add("https://a/", interval_seconds=60)
    await store.set_enabled(row.id, False)
    assert await store.due(now=10_000.0) == []


async def test_record_check_stamps_changed(store):
    row = await store.add("https://a/", interval_seconds=60)
    await store.record_check(row.id, run_id="r2", checked_at=500.0, changed=True)
    fetched = await store.get(row.id)
    assert fetched.last_run_id == "r2"
    assert fetched.last_checked_at == 500.0
    assert fetched.last_changed_at == 500.0


# --- scheduler -------------------------------------------------------------

def _outcome(row: WatchRow, *, changed: bool) -> CheckOutcome:
    return CheckOutcome(
        watch_id=row.id, url=row.url, changed=changed, not_modified=not changed,
        run_id="run-" + row.id[:4], summary="...", field_changes=["price: '$1' -> '$2'"] if changed else [],
    )


async def test_tick_checks_due_and_advances_cursor(store, config):
    row = await store.add("https://a/", interval_seconds=100)
    calls = []

    async def check_fn(r):
        calls.append(r.id)
        return _outcome(r, changed=True)

    sched = WatchScheduler(store, config=config, check_fn=check_fn, clock=lambda: 2000.0)
    outcomes = await sched.tick()
    assert calls == [row.id]
    assert outcomes[0].changed is True
    # Cursor advanced → not due again at the same time.
    assert await store.due(now=2000.0) == []


async def test_tick_fires_notifier_only_on_change(store, config):
    changed_row = await store.add("https://changed/", interval_seconds=100)
    same_row = await store.add("https://same/", interval_seconds=100)
    notified = []

    async def check_fn(r):
        return _outcome(r, changed=(r.id == changed_row.id))

    notifier = CallbackNotifier(lambda w, o: notified.append(o.url))
    sched = WatchScheduler(store, config=config, check_fn=check_fn, notifier=notifier,
                           clock=lambda: 3000.0)
    await sched.tick()
    assert notified == ["https://changed/"]
    _ = same_row  # present but unchanged → no notification


async def test_tick_isolates_a_failing_check(store, config):
    bad = await store.add("https://bad/", interval_seconds=100)
    await store.add("https://good/", interval_seconds=100)

    async def check_fn(r):
        if r.id == bad.id:
            raise RuntimeError("scrape blew up")
        return _outcome(r, changed=True)

    sched = WatchScheduler(store, config=config, check_fn=check_fn, clock=lambda: 4000.0)
    outcomes = await sched.tick()
    # The good watch still produced an outcome; the bad one was swallowed.
    assert [o.url for o in outcomes] == ["https://good/"]
    # The bad watch's cursor still advanced so it won't hot-loop.
    bad_after = await store.get(bad.id)
    assert bad_after.last_checked_at == 4000.0


async def test_run_forever_stops_on_event(store, config):
    import asyncio

    await store.add("https://a/", interval_seconds=100)
    ticks = []

    async def check_fn(r):
        ticks.append(r.id)
        return _outcome(r, changed=False)

    stop = asyncio.Event()
    sched = WatchScheduler(store, config=config, check_fn=check_fn, clock=lambda: 5000.0)

    async def stopper():
        await asyncio.sleep(0.01)
        stop.set()

    await asyncio.gather(
        sched.run_forever(poll_seconds=0.005, stop=stop),
        stopper(),
    )
    assert len(ticks) >= 1  # ran at least one tick before stopping
