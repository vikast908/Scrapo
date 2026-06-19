"""Field-level diff between two recorded runs."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any

from scrapo.replay.store import ReplayStore


@dataclass(slots=True)
class FieldDiff:
    field: str
    before: Any
    after: Any

    def __str__(self) -> str:
        return f"{self.field}: {self.before!r} → {self.after!r}"


@dataclass(slots=True)
class DiffReport:
    run_a: str
    run_b: str
    same_url: bool
    same_html: bool
    same_method: bool
    field_changes: list[FieldDiff]
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "same_url": self.same_url,
            "same_html": self.same_html,
            "same_method": self.same_method,
            "field_changes": [
                {"field": d.field, "before": d.before, "after": d.after}
                for d in self.field_changes
            ],
            "notes": self.notes,
        }


async def diff_runs(store: ReplayStore, run_a: str, run_b: str) -> DiffReport:
    a = await store.get(run_a)
    b = await store.get(run_b)
    if a is None:
        raise ValueError(f"unknown run_id: {run_a}")
    if b is None:
        raise ValueError(f"unknown run_id: {run_b}")

    notes: list[str] = []
    if a["model_pinned"] != b["model_pinned"]:
        notes.append(
            f"model changed: {a['model_pinned']} → {b['model_pinned']} (extraction may drift)"
        )
    if a["schema_version"] != b["schema_version"]:
        notes.append(f"schema version changed: {a['schema_version']} → {b['schema_version']}")
    if a["tier_used"] != b["tier_used"]:
        notes.append(f"tier changed: {a['tier_used']} → {b['tier_used']}")

    data_a = _extract_data(a.get("extraction_json"))
    data_b = _extract_data(b.get("extraction_json"))
    field_changes = _diff(data_a, data_b)

    return DiffReport(
        run_a=run_a,
        run_b=run_b,
        same_url=a["url"] == b["url"],
        same_html=await _same_html(store, a, b),
        same_method=a["extraction_method"] == b["extraction_method"],
        field_changes=field_changes,
        notes=notes,
    )


def diff_summary(report: DiffReport) -> str:
    lines = [f"diff {report.run_a[:8]}…  vs  {report.run_b[:8]}…"]
    if not report.same_url:
        lines.append("  URLs differ")
    if report.same_html:
        lines.append("  HTML identical (extraction-only drift)")
    else:
        lines.append("  HTML changed")
    for note in report.notes:
        lines.append(f"  ! {note}")
    if not report.field_changes:
        lines.append("  no field changes")
    else:
        lines.append("  field changes:")
        for d in report.field_changes:
            lines.append(f"    - {d}")
    return "\n".join(lines)


def _extract_data(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return {}
    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict):
        return data
    return {}


async def _same_html(store: ReplayStore, a: dict[str, Any], b: dict[str, Any]) -> bool:
    pa, pb = a.get("html_path"), b.get("html_path")
    if not (pa and pb):
        return False
    if pa == pb:
        # The conditional-GET path points two runs at the same archived snapshot.
        return True
    # No html_hash is persisted on the run record, so we must read both blobs.
    # Compare by SHA-256 rather than a full O(n) python-level byte equality:
    # this avoids holding both blobs for the comparison and is clearer for large
    # gzipped HTML. The (blocking) snapshot reads run off the event loop.
    ba = await asyncio.to_thread(store.snapshots.get, pa)
    bb = await asyncio.to_thread(store.snapshots.get, pb)
    if ba is None or bb is None:
        return False
    ha = hashlib.sha256(ba).hexdigest()
    hb = hashlib.sha256(bb).hexdigest()
    return ha == hb


def _diff(a: dict[str, Any], b: dict[str, Any]) -> list[FieldDiff]:
    keys = sorted(set(a.keys()) | set(b.keys()))
    return [FieldDiff(k, a.get(k), b.get(k)) for k in keys if a.get(k) != b.get(k)]
