"""Export scrape / batch results to JSONL or CSV.

``batch_scrape`` and ``crawl`` produce typed results; this turns a collection of
them into a flat dataset file so Scrapo drops into a data pipeline without glue
code. Both writers accept a mix of :class:`~scrapo.results.ScrapeResult` and
:class:`~scrapo.crawl.batch.BatchItem` (a batch item that errored becomes a row
with its ``error`` set and the rest null).

* :func:`to_jsonl` — one JSON object per line, full-fidelity record. Optionally
  include the markdown body.
* :func:`to_csv` — a flat table. Base columns plus one column per top-level
  scalar field found in any record's extraction; nested values are JSON-encoded
  into the cell.

Stdlib only — no new dependencies.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from scrapo.crawl.batch import BatchItem
from scrapo.results import ScrapeResult

ResultLike = ScrapeResult | BatchItem

_BASE_COLUMNS = (
    "url",
    "run_id",
    "status",
    "tier_used",
    "kind",
    "title",
    "blocked",
    "block_reason",
    "cost_usd",
    "error",
)


def _record(item: ResultLike, *, include_markdown: bool) -> dict[str, Any]:
    """Normalise one result into a flat-ish record dict."""
    if isinstance(item, BatchItem):
        if item.result is None:
            return {"url": item.url, "error": item.error}
        inner = _record(item.result, include_markdown=include_markdown)
        inner.setdefault("url", item.url)
        inner["error"] = item.error
        return inner

    res = item  # narrowed to ScrapeResult
    extraction = res.extraction.data if res.extraction is not None else None
    rec: dict[str, Any] = {
        "url": res.url,
        "run_id": res.run_id,
        "status": res.status,
        "tier_used": res.tier_used,
        "kind": res.kind,
        "title": res.title,
        "blocked": res.blocked,
        "block_reason": res.block_reason,
        "cost_usd": res.cost_usd,
        "error": None,
        "extraction": extraction,
        "data": res.data,
    }
    if include_markdown:
        rec["markdown"] = res.markdown
    return rec


def to_jsonl(
    results: Iterable[ResultLike],
    path: str | Path,
    *,
    include_markdown: bool = False,
) -> int:
    """Write ``results`` as JSON Lines. Returns the number of records written."""
    out = Path(path)
    count = 0
    lines: list[str] = []
    for item in results:
        rec = _record(item, include_markdown=include_markdown)
        lines.append(json.dumps(rec, ensure_ascii=False, default=str))
        count += 1
    out.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return count


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False, default=str)


def to_csv(results: Iterable[ResultLike], path: str | Path) -> int:
    """Write ``results`` as a flat CSV. Returns the number of rows written.

    Columns are the base metadata fields plus one column per top-level scalar
    field seen in any record's extraction payload (collisions with a base column
    are dropped so a base value is never shadowed). Nested extraction values are
    JSON-encoded into their cell.
    """
    records = [_record(item, include_markdown=False) for item in results]

    # Discover extraction columns across every record (stable, sorted, no clashes
    # with the reserved base columns).
    extra_keys: list[str] = []
    seen: set[str] = set(_BASE_COLUMNS)
    for rec in records:
        extraction = rec.get("extraction")
        if isinstance(extraction, dict):
            for key in extraction:
                if key not in seen:
                    seen.add(key)
                    extra_keys.append(key)
    extra_keys.sort()
    columns = list(_BASE_COLUMNS) + extra_keys

    out = Path(path)
    with out.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for rec in records:
            extraction = rec.get("extraction")
            row: list[str] = [_cell(rec.get(col)) for col in _BASE_COLUMNS]
            for key in extra_keys:
                value = extraction.get(key) if isinstance(extraction, dict) else None
                row.append(_cell(value))
            writer.writerow(row)
    return len(records)
