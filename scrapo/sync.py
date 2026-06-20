"""Synchronous facade over the async public API.

Scrapo is async at its core, but plenty of callers live in synchronous code —
scripts, notebooks, small data pipelines, ``if __name__ == '__main__'`` glue.
These wrappers let them call Scrapo without writing ``asyncio.run`` boilerplate::

    import scrapo

    res = scrapo.scrape_sync("https://example.com/")
    print(res.markdown)

Each ``*_sync`` function has the exact signature of its async twin (preserved via
``ParamSpec``) and simply drives it to completion. When called from *outside* an
event loop it uses :func:`asyncio.run`; when called from *inside* one (e.g. a
Jupyter cell, which already runs a loop) it transparently runs the coroutine on a
dedicated worker thread so it doesn't raise "asyncio.run() cannot be called from
a running event loop".

Only the buffered entry points are wrapped. The streaming generators
(``crawl_stream`` / ``batch_scrape_stream``) stay async-only — a synchronous
generator bridge would defeat their whole point.
"""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any, ParamSpec, TypeVar

from scrapo.api import batch_scrape, crawl, extract, map_site, scrape

P = ParamSpec("P")
R = TypeVar("R")


def _run(make_coro: Callable[[], Coroutine[Any, Any, R]]) -> R:
    """Run ``make_coro()`` to completion from synchronous code.

    Fast path: no loop running → :func:`asyncio.run`. Fallback: a loop is already
    running on this thread (notebook / nested call) → run on a fresh worker thread
    with its own loop, propagating the result or exception back.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(make_coro())

    import threading

    box: dict[str, Any] = {}

    def runner() -> None:
        try:
            box["result"] = asyncio.run(make_coro())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            box["error"] = exc

    thread = threading.Thread(target=runner, name="scrapo-sync")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]  # type: ignore[no-any-return]


def _wrap(fn: Callable[P, Coroutine[Any, Any, R]]) -> Callable[P, R]:
    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        return _run(lambda: fn(*args, **kwargs))

    wrapper.__name__ = f"{fn.__name__}_sync"
    wrapper.__qualname__ = wrapper.__name__
    return wrapper


scrape_sync = _wrap(scrape)
extract_sync = _wrap(extract)
crawl_sync = _wrap(crawl)
map_site_sync = _wrap(map_site)
batch_scrape_sync = _wrap(batch_scrape)


__all__ = [
    "batch_scrape_sync",
    "crawl_sync",
    "extract_sync",
    "map_site_sync",
    "scrape_sync",
]
