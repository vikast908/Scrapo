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
