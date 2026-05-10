"""Watch a URL for changes — a thin loop over :func:`scrapo.scrape` + :func:`diff_runs`.

``watch(url, schema=...)`` does an initial scrape and hands back a :class:`Watch`.
Call ``await w.refresh()`` whenever you want to re-check it: that re-scrapes
(conditional GET kicks in automatically, so an unchanged page costs ~nothing and
skips the LLM) and returns a :class:`ChangeSet` describing what moved — built on
the same field-level diff the replay store already produces.

This is in-process: the ``Watch`` object holds the last run id, and the
underlying runs/snapshots live in the replay store. Persisting a watch *list*
across processes (with a scheduler) is a deployable-service concern and is left
out of the library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from scrapo.api import scrape
from scrapo.config import Config, get_config
from scrapo.replay.diff import DiffReport, FieldDiff, diff_runs, diff_summary
from scrapo.replay.store import ReplayStore
from scrapo.results import ScrapeResult


@dataclass(slots=True)
class ChangeSet:
    """What changed between a watch's previous run and its latest one."""

    url: str
    changed: bool  # fields and/or the page body differ
    not_modified: bool  # the server answered a 304 — definitively unchanged
    result: ScrapeResult  # the fresh scrape
    diff: DiffReport | None = None  # None on the very first run (nothing to compare)

    @property
    def field_changes(self) -> list[FieldDiff]:
        return list(self.diff.field_changes) if self.diff else []

    def summary(self) -> str:
        if self.not_modified:
            return f"{self.url}: not modified (304)"
        if self.diff is None:
            return f"{self.url}: first run, nothing to compare"
        return diff_summary(self.diff)


@dataclass
class Watch:
    """A handle for re-checking a single URL. Create via :func:`watch`."""

    url: str
    schema: type[BaseModel] | None = None
    config: Config | None = None
    scrape_kwargs: dict[str, Any] = field(default_factory=dict)  # pin=, force_tier=, ...
    last_run_id: str | None = None
    last: ScrapeResult | None = None
    _store: ReplayStore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.config = self.config or get_config()
        self._store = ReplayStore(self.config)

    async def _scrape_once(self) -> ScrapeResult:
        return await scrape(self.url, schema=self.schema, config=self.config, **self.scrape_kwargs)

    async def check(self) -> ScrapeResult:
        """Scrape now and remember the run, without computing a diff."""
        result = await self._scrape_once()
        self.last, self.last_run_id = result, result.run_id
        return result

    async def refresh(self) -> ChangeSet:
        """Re-scrape and return what changed since the last run."""
        prev_run_id = self.last_run_id
        result = await self._scrape_once()
        not_modified = bool(result.not_modified)
        diff: DiffReport | None = None
        if prev_run_id and prev_run_id != result.run_id:
            diff = await diff_runs(self._store, prev_run_id, result.run_id)
        changed = (
            not not_modified
            and diff is not None
            and (bool(diff.field_changes) or not diff.same_html)
        )
        self.last, self.last_run_id = result, result.run_id
        return ChangeSet(
            url=self.url, changed=changed, not_modified=not_modified, result=result, diff=diff
        )


async def watch(
    url: str,
    *,
    schema: type[BaseModel] | None = None,
    config: Config | None = None,
    **scrape_kwargs: Any,
) -> Watch:
    """Create a :class:`Watch`, do its first scrape, and return it."""
    w = Watch(url=url, schema=schema, config=config, scrape_kwargs=scrape_kwargs)
    await w.check()
    return w
