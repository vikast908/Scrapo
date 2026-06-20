"""JSONL / CSV exporters."""

import csv
import json

from scrapo.crawl.batch import BatchItem
from scrapo.export import to_csv, to_jsonl
from scrapo.results import ExtractionView, ScrapeResult


def _result(url, *, name=None, price=None, status=200):
    extraction = None
    if name is not None:
        extraction = ExtractionView(data={"name": name, "price": price}, method="metadata")
    return ScrapeResult(
        run_id="r-" + url[-1],
        url=url,
        status=status,
        tier_used="http",
        kind="html",
        title="T-" + url[-1],
        markdown="# body",
        extraction=extraction,
        cost_usd=0.0,
    )


def test_to_jsonl_writes_one_object_per_line(tmp_path):
    out = tmp_path / "out.jsonl"
    n = to_jsonl([_result("https://a/1", name="A"), _result("https://b/2", name="B")], out)
    assert n == 2
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["url"] == "https://a/1"
    assert first["extraction"] == {"name": "A", "price": None}
    assert "markdown" not in first  # excluded by default


def test_to_jsonl_optional_markdown(tmp_path):
    out = tmp_path / "out.jsonl"
    to_jsonl([_result("https://a/1", name="A")], out, include_markdown=True)
    rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
    assert rec["markdown"] == "# body"


def test_to_jsonl_empty(tmp_path):
    out = tmp_path / "out.jsonl"
    assert to_jsonl([], out) == 0
    assert out.read_text(encoding="utf-8") == ""


def test_to_csv_flattens_extraction_columns(tmp_path):
    out = tmp_path / "out.csv"
    n = to_csv(
        [_result("https://a/1", name="A", price="$1"), _result("https://b/2", name="B")], out
    )
    assert n == 2
    rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
    assert rows[0]["url"] == "https://a/1"
    assert rows[0]["name"] == "A"
    assert rows[0]["price"] == "$1"
    assert rows[1]["name"] == "B"
    assert rows[1]["price"] == ""  # missing → empty cell
    # base columns present
    assert rows[0]["status"] == "200"
    assert rows[0]["tier_used"] == "http"


def test_to_csv_nested_value_is_json_encoded(tmp_path):
    out = tmp_path / "out.csv"
    res = ScrapeResult(
        run_id="r1",
        url="https://a/1",
        extraction=ExtractionView(data={"tags": [{"label": "x"}, {"label": "y"}]}),
    )
    to_csv([res], out)
    row = next(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
    assert json.loads(row["tags"]) == [{"label": "x"}, {"label": "y"}]


def test_batch_items_export_including_errors(tmp_path):
    items = [
        BatchItem(url="https://ok/1", result=_result("https://ok/1", name="A"), error=None),
        BatchItem(url="https://bad/2", result=None, error="boom"),
    ]
    out = tmp_path / "b.jsonl"
    to_jsonl(items, out)
    recs = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert recs[0]["url"] == "https://ok/1"
    assert recs[0]["error"] is None
    assert recs[1]["url"] == "https://bad/2"
    assert recs[1]["error"] == "boom"

    csv_out = tmp_path / "b.csv"
    to_csv(items, csv_out)
    rows = list(csv.DictReader(csv_out.read_text(encoding="utf-8").splitlines()))
    assert rows[1]["url"] == "https://bad/2"
    assert rows[1]["error"] == "boom"
    assert rows[1]["name"] == ""
