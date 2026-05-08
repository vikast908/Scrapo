import pytest

from scrapo.crawl.queue import RequestQueue


@pytest.mark.asyncio
async def test_enqueue_claim_complete(tmp_path):
    q = RequestQueue(tmp_path / "queue.sqlite", crawl_id="t1")
    assert await q.enqueue("https://e.com/a")
    assert not await q.enqueue("https://e.com/a")  # dup
    assert await q.enqueue("https://e.com/b")

    r1 = await q.claim()
    assert r1 and r1["url"] == "https://e.com/a"
    r2 = await q.claim()
    assert r2 and r2["url"] == "https://e.com/b"
    assert (await q.claim()) is None

    await q.complete(r1["id"])
    stats = await q.stats()
    assert stats.get("done", 0) == 1
    assert stats.get("in_flight", 0) == 1


@pytest.mark.asyncio
async def test_fail_with_retry(tmp_path):
    q = RequestQueue(tmp_path / "queue.sqlite", crawl_id="t2")
    await q.enqueue("https://e.com/x")
    r = await q.claim()
    await q.fail(r["id"], "boom", retry=True)
    r2 = await q.claim()
    assert r2 and r2["url"] == "https://e.com/x"
    assert r2["attempts"] == 2
