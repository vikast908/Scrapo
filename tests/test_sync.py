"""Synchronous facade over the async API."""

import asyncio

import pytest

import scrapo
from scrapo.sync import _run, _wrap


async def _double(x: int) -> int:
    await asyncio.sleep(0)
    return x * 2


async def _boom() -> None:
    await asyncio.sleep(0)
    raise ValueError("kaboom")


def test_run_executes_coroutine_no_loop():
    assert _run(lambda: _double(21)) == 42


def test_run_propagates_exception():
    with pytest.raises(ValueError, match="kaboom"):
        _run(lambda: _boom())


def test_wrap_preserves_name_and_runs():
    doubled = _wrap(_double)
    assert doubled.__name__ == "_double_sync"
    assert doubled(5) == 10


async def test_wrapper_works_inside_running_loop():
    # We're already inside an event loop here (asyncio_mode=auto). The wrapper must
    # fall back to a worker thread instead of raising "cannot be called from a
    # running event loop".
    doubled = _wrap(_double)
    assert doubled(8) == 16


async def test_wrapper_propagates_exception_inside_loop():
    boom = _wrap(_boom)
    with pytest.raises(ValueError, match="kaboom"):
        boom()


def test_public_sync_api_is_exported():
    for name in (
        "scrape_sync",
        "extract_sync",
        "crawl_sync",
        "map_site_sync",
        "batch_scrape_sync",
    ):
        assert hasattr(scrapo, name), name
        assert callable(getattr(scrapo, name))
