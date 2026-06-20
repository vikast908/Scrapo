"""Auto-pagination verbs: scroll_until / click_until."""

from typing import Any

import pytest

from scrapo.access.actions import Action, coerce_actions, run_actions


class _Mouse:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def wheel(self, dx: int, dy: int) -> None:
        self._page.wheels += 1


class _FakePage:
    """Scriptable page. ``counts``/``heights`` drive the scroll progress signal;
    ``button_rounds`` is how many clicks until the load-more button disappears."""

    def __init__(
        self,
        *,
        counts: list[int] | None = None,
        heights: list[int] | None = None,
        button_rounds: int | None = None,
    ) -> None:
        self.mouse = _Mouse(self)
        self.wheels = 0
        self.waits = 0
        self.clicks = 0
        self._counts = list(counts) if counts is not None else None
        self._heights = list(heights) if heights is not None else None
        self._button_rounds = button_rounds

    async def wait_for_timeout(self, ms: int) -> None:
        self.waits += 1

    async def query_selector_all(self, selector: str) -> list[Any]:
        n = self._counts.pop(0) if self._counts else 0
        return [object()] * n

    async def evaluate(self, expr: str) -> int:
        return self._heights.pop(0) if self._heights else 0

    async def query_selector(self, selector: str) -> Any:
        if self._button_rounds is not None and self.clicks >= self._button_rounds:
            return None
        return object()

    async def click(self, selector: str, **_kw: Any) -> None:
        self.clicks += 1


# --- validation ------------------------------------------------------------

def test_click_until_requires_selector():
    with pytest.raises(ValueError, match="'click_until' requires 'selector'"):
        Action(type="click_until")


def test_coerce_accepts_new_verbs_and_times_field():
    out = coerce_actions(
        [
            {"type": "scroll_until", "times": 5, "amount": 1500, "ms": 100},
            {"type": "click_until", "selector": "button.more", "times": 3},
        ]
    )
    assert [a.type for a in out] == ["scroll_until", "click_until"]
    assert out[0].times == 5
    assert out[1].selector == "button.more"


# --- scroll_until ----------------------------------------------------------

async def test_scroll_until_stops_when_selector_count_stabilises():
    # counts grow 3 -> 6 then flatten at 6: the 3rd measure (==prev) ends it.
    page = _FakePage(counts=[3, 6, 6])
    await run_actions(page, [Action(type="scroll_until", selector=".card")])
    assert page.wheels == 3


async def test_scroll_until_uses_page_height_without_selector():
    page = _FakePage(heights=[1000, 2000, 2000])
    await run_actions(page, [Action(type="scroll_until")])
    assert page.wheels == 3


async def test_scroll_until_respects_round_cap():
    # Content keeps growing forever, but `times` bounds it at 2 rounds.
    page = _FakePage(counts=[5, 10, 15, 20, 25], button_rounds=None)
    await run_actions(page, [Action(type="scroll_until", selector=".card", times=2)])
    assert page.wheels == 2


# --- click_until -----------------------------------------------------------

async def test_click_until_clicks_until_button_gone():
    page = _FakePage(button_rounds=3)
    summary = await run_actions(page, [Action(type="click_until", selector="button.more")])
    assert page.clicks == 3
    assert summary["executed"] == ["click_until"]
    assert summary["errors"] == []


async def test_click_until_respects_round_cap_when_button_persists():
    page = _FakePage(button_rounds=None)  # button never disappears
    await run_actions(page, [Action(type="click_until", selector="button.more", times=4)])
    assert page.clicks == 4
