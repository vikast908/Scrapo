from typing import Any

import pytest

from scrapo.access.actions import Action, coerce_actions, run_actions

# --- fakes -----------------------------------------------------------------

class _Mouse:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def wheel(self, dx: int, dy: int) -> None:
        self._page.events.append(("wheel", dx, dy))


class _FakePage:
    def __init__(self, fail_selectors: set[str] | None = None) -> None:
        self.events: list[tuple] = []
        self.mouse = _Mouse(self)
        self._fail_selectors = fail_selectors or set()

    async def goto(self, url: str, **_kw: Any) -> None:
        self.events.append(("goto", url))

    async def click(self, selector: str, **_kw: Any) -> None:
        if selector in self._fail_selectors:
            raise RuntimeError(f"no such element: {selector}")
        self.events.append(("click", selector))

    async def type(self, selector: str, text: str) -> None:
        self.events.append(("type", selector, text))

    async def fill(self, selector: str, text: str) -> None:
        self.events.append(("fill", selector, text))

    async def press(self, selector: str, key: str) -> None:
        self.events.append(("press", selector, key))

    async def wait_for_timeout(self, ms: int) -> None:
        self.events.append(("wait", ms))

    async def wait_for_selector(self, selector: str, **_kw: Any) -> None:
        self.events.append(("wait_for_selector", selector))

    async def screenshot(self, **_kw: Any) -> bytes:
        self.events.append(("screenshot",))
        return b""


# --- Action validation -----------------------------------------------------

def test_action_post_init_accepts_valid():
    assert Action(type="click", selector="#a").type == "click"
    assert Action(type="goto", url="https://x.tld/").url == "https://x.tld/"
    assert Action(type="type", selector="#u", text="alice").text == "alice"
    assert Action(type="press", key="Enter").key == "Enter"
    assert Action(type="fill", selector="#u", text="").text == ""  # empty text ok for fill


def test_action_post_init_rejects_bad():
    with pytest.raises(ValueError, match="unknown action type"):
        Action(type="teleport")
    with pytest.raises(ValueError, match="'goto' requires 'url'"):
        Action(type="goto")
    with pytest.raises(ValueError, match="requires 'selector'"):
        Action(type="click")
    with pytest.raises(ValueError, match="requires 'selector'"):
        Action(type="type", text="hi")
    with pytest.raises(ValueError, match="requires 'text'"):
        Action(type="fill", selector="#u")
    with pytest.raises(ValueError, match="'press' requires 'key'"):
        Action(type="press")


# --- coerce_actions --------------------------------------------------------

def test_coerce_actions_from_dicts():
    out = coerce_actions(
        [
            {"type": "goto", "url": "https://x.tld/"},
            {"type": "click", "selector": "#go"},
            Action(type="scroll", amount=100),
        ]
    )
    assert [a.type for a in out] == ["goto", "click", "scroll"]
    assert out[0].url == "https://x.tld/"
    assert out[2].amount == 100


def test_coerce_actions_rejects_malformed():
    with pytest.raises(ValueError, match="missing 'type'"):
        coerce_actions([{"selector": "#a"}])
    with pytest.raises(ValueError, match="unknown action field"):
        coerce_actions([{"type": "click", "selector": "#a", "bogus": 1}])
    with pytest.raises(ValueError, match="must be an Action or dict"):
        coerce_actions(["click #a"])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="unknown action type"):
        coerce_actions([{"type": "nope"}])


# --- run_actions -----------------------------------------------------------

async def test_run_actions_executes_sequence_in_order():
    page = _FakePage()
    actions = [
        Action(type="click", selector="#btn"),
        Action(type="type", selector="#u", text="alice"),
        Action(type="fill", selector="#p", text="secret"),
        Action(type="scroll", amount=300),
        Action(type="wait", ms=50),
        Action(type="press", key="Enter"),
        Action(type="wait_for_selector", selector="#done"),
        Action(type="screenshot"),
    ]
    summary = await run_actions(page, actions)
    assert summary["steps"] == 8
    assert summary["errors"] == []
    assert summary["executed"] == [
        "click",
        "type",
        "fill",
        "scroll",
        "wait",
        "press",
        "wait_for_selector",
        "screenshot",
    ]
    assert page.events == [
        ("click", "#btn"),
        ("type", "#u", "alice"),
        ("fill", "#p", "secret"),
        ("wheel", 0, 300),
        ("wait", 50),
        ("press", "body", "Enter"),  # press defaults to body
        ("wait_for_selector", "#done"),
        ("screenshot",),
    ]


async def test_run_actions_blocks_ssrf_goto():
    page = _FakePage()
    actions = [
        Action(type="goto", url="http://127.0.0.1/"),
        Action(type="goto", url="https://example.com/ok"),
        Action(type="goto", url="http://169.254.169.254/latest/meta-data/"),
    ]
    summary = await run_actions(page, actions)
    assert summary["steps"] == 3
    # only the public goto navigated
    assert page.events == [("goto", "https://example.com/ok")]
    assert summary["executed"] == ["goto"]
    assert len(summary["errors"]) == 2
    assert all("blocked" in e for e in summary["errors"])


async def test_run_actions_allow_private_lets_goto_through():
    page = _FakePage()
    summary = await run_actions(
        page, [Action(type="goto", url="http://127.0.0.1/")], allow_private=True
    )
    assert page.events == [("goto", "http://127.0.0.1/")]
    assert summary["errors"] == []


async def test_run_actions_isolates_failing_step():
    page = _FakePage(fail_selectors={"#broken"})
    actions = [
        Action(type="click", selector="#ok1"),
        Action(type="click", selector="#broken"),
        Action(type="click", selector="#ok2"),
    ]
    summary = await run_actions(page, actions)
    assert summary["steps"] == 3
    assert summary["executed"] == ["click", "click"]  # the broken one omitted
    assert len(summary["errors"]) == 1
    assert "#broken" in summary["errors"][0]
    # the rest still ran
    assert ("click", "#ok1") in page.events
    assert ("click", "#ok2") in page.events
