import time

from scrapo.replay.diff import diff_runs, diff_summary
from scrapo.replay.store import ReplayStore
from scrapo.types import ExtractionResult, FetchResult, RunRecord, Tier


async def _fake_run(store: ReplayStore, *, url: str, html: str, name: str):
    record = RunRecord.new(url)
    record.tier_used = Tier.HTTP
    record.fetch_status = 200
    record.extraction_method = "llm"
    record.model_pinned = "fake:fake-1"
    record.schema_version = "Product@abc"
    record.finished_at = time.time()
    fetch = FetchResult(
        url=url,
        final_url=url,
        status=200,
        html=html,
        headers={"content-type": "text/html"},
        tier_used=Tier.HTTP,
    )
    extraction = ExtractionResult(
        data={"name": name, "price": "$42"},
        method="llm",
        schema_version="Product@abc",
        model_pinned="fake:fake-1",
    )
    await store.record(record, fetch, extraction)
    return record.run_id


async def test_record_and_load(isolated_config):
    store = ReplayStore(isolated_config)
    rid = await _fake_run(store, url="https://e.com/a", html="<html>hi</html>", name="A")
    rec = await store.get(rid)
    assert rec is not None
    assert rec["url"] == "https://e.com/a"
    html = await store.load_html(rid)
    assert html == "<html>hi</html>"


async def test_diff_field_change(isolated_config):
    store = ReplayStore(isolated_config)
    a = await _fake_run(store, url="https://e.com/a", html="<html>v1</html>", name="A")
    b = await _fake_run(store, url="https://e.com/a", html="<html>v2</html>", name="B")
    report = await diff_runs(store, a, b)
    assert any(d.field == "name" for d in report.field_changes)
    summary = diff_summary(report)
    assert "field changes" in summary


async def test_record_persists_validators_and_last_run(isolated_config):
    store = ReplayStore(isolated_config)
    rec = RunRecord.new("https://e.com/validated")
    rec.tier_used = Tier.HTTP
    rec.fetch_status = 200
    rec.etag = '"abc"'
    rec.last_modified = "Wed, 21 Oct 2026 07:28:00 GMT"
    rec.finished_at = time.time()
    fetch = FetchResult(
        url="https://e.com/validated", final_url="https://e.com/validated",
        status=200, html="<html>hi</html>", headers={"etag": '"abc"'}, tier_used=Tier.HTTP,
    )
    await store.record(rec, fetch, None)
    last = await store.last_run("https://e.com/validated")
    assert last is not None
    assert last["run_id"] == rec.run_id
    assert last["etag"] == '"abc"'
    assert last["last_modified"].startswith("Wed,")
    assert last["not_modified"] == 0
    assert await store.last_run("https://e.com/never-seen") is None


async def test_record_reuses_html_path(isolated_config):
    store = ReplayStore(isolated_config)
    first = RunRecord.new("https://e.com/reuse")
    first.tier_used, first.fetch_status, first.finished_at = Tier.HTTP, 200, time.time()
    fetch = FetchResult(
        url="https://e.com/reuse", final_url="https://e.com/reuse",
        status=200, html="<html>body</html>", headers={}, tier_used=Tier.HTTP,
    )
    await store.record(first, fetch, None)
    path = (await store.get(first.run_id))["html_path"]
    assert path

    second = RunRecord.new("https://e.com/reuse")
    second.tier_used, second.fetch_status, second.finished_at, second.not_modified = Tier.HTTP, 200, time.time(), True
    await store.record(second, fetch, None, html_path=path)
    assert (await store.get(second.run_id))["html_path"] == path
    assert (await store.get(second.run_id))["not_modified"] == 1
