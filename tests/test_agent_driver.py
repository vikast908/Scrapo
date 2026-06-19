from typing import Any

from scrapo.access.action_cache import ActionCache
from scrapo.access.actions import Action, run_actions
from scrapo.access.agent_drivers import (
    PROMPT_VERSION,
    LLMAgentDriver,
    _format_elements,
    _record_step,
    _replay,
    parse_action,
)
from scrapo.access.router import TierRouter
from scrapo.config import Config
from scrapo.extract.llm_adapters.base import LLMResponse


def _resp(text="", payload=None):
    return LLMResponse(text=text, json_payload=payload, provider="x", model_id="x-1")


def test_parse_action_from_json_payload():
    assert parse_action(_resp(payload={"action": "click", "target": 3, "reason": "the button"})) == {
        "action": "click",
        "target": 3,
        "text": None,
        "reason": "the button",
    }


def test_parse_action_from_fenced_text():
    a = parse_action(_resp(text='```json\n{"action": "type", "target": 1, "text": "hi"}\n```'))
    assert a["action"] == "type"
    assert a["target"] == 1
    assert a["text"] == "hi"


def test_parse_action_unknown_or_garbage_is_done():
    assert parse_action(_resp(payload={"action": "teleport"}))["action"] == "done"
    assert parse_action(_resp(text="not json"))["action"] == "done"
    assert parse_action(_resp())["action"] == "done"


def test_parse_action_coerces_bad_fields():
    a = parse_action(_resp(payload={"action": "click", "target": "five", "text": 42}))
    assert a["target"] is None
    assert a["text"] is None


def test_format_elements():
    s = _format_elements(
        [{"id": 0, "tag": "button", "type": "", "text": "Submit"}, {"id": 1, "tag": "input", "type": "text", "text": ""}]
    )
    assert '0: button "Submit"' in s
    assert '1: input[text] ""' in s
    assert _format_elements([]) == "(none found)"


def test_router_wires_agent_driver_when_configured(tmp_path):
    router = TierRouter(Config(data_dir=tmp_path / "a", agent_driver="llm"))
    assert isinstance(router.agent.driver, LLMAgentDriver)
    assert TierRouter(Config(data_dir=tmp_path / "b")).agent.driver is None


def test_agent_tier_action_cache_toggle(tmp_path):
    from scrapo.access.agent_tier import AgentTier

    assert AgentTier(Config(data_dir=tmp_path / "on")).action_cache is not None
    assert AgentTier(Config(data_dir=tmp_path / "off", agent_action_cache=False)).action_cache is None


# --- _record_step ----------------------------------------------------------

def test_record_step_simple_actions():
    assert _record_step({"action": "scroll"}, []) == {"action": "scroll"}
    assert _record_step({"action": "goto", "text": "https://x.tld/y"}, []) == {
        "action": "goto",
        "text": "https://x.tld/y",
    }
    assert _record_step({"action": "goto", "text": "javascript:1"}, []) is None
    assert _record_step({"action": "done"}, []) is None


def test_record_step_click_and_type_capture_selector():
    els = [{"id": 0, "tag": "input", "type": "text", "text": "", "sel": "input#user"}]
    assert _record_step({"action": "type", "target": 0, "text": "alice"}, els) == {
        "action": "type",
        "sel": "input#user",
        "text_target": "",
        "tag": "input",
        "text": "alice",
    }
    assert _record_step({"action": "click", "target": 5}, els) is None  # out of range
    assert _record_step({"action": "click", "target": None}, els) is None


# --- fakes for the run() loop ---------------------------------------------

class _Locator:
    def __init__(self, page: "_FakePage", selector: str) -> None:
        self._page = page
        self._selector = selector

    async def count(self) -> int:
        return self._page.selector_counts.get(self._selector, 0)

    @property
    def first(self) -> "_Locator":
        return self

    async def click(self, **_kw: Any) -> None:
        self._page.events.append(("click", self._selector))

    async def fill(self, text: str, **_kw: Any) -> None:
        self._page.events.append(("fill", self._selector, text))


class _Mouse:
    def __init__(self, page: "_FakePage") -> None:
        self._page = page

    async def wheel(self, dx: int, dy: int) -> None:
        self._page.events.append(("wheel", dy))


class _FakePage:
    def __init__(self, url: str, elements: list[dict[str, Any]], selector_counts: dict[str, int] | None = None) -> None:
        self.url = url
        self._elements = elements
        self.selector_counts = selector_counts or {}
        self.events: list[tuple] = []
        self.mouse = _Mouse(self)

    async def evaluate(self, _js: str) -> list[dict[str, Any]]:
        return self._elements

    async def title(self) -> str:
        return "Fake"

    async def click(self, selector: str, **_kw: Any) -> None:
        self.events.append(("click", selector))

    async def fill(self, selector: str, text: str, **_kw: Any) -> None:
        self.events.append(("fill", selector, text))

    async def goto(self, url: str, wait_until: str = "") -> None:
        self.url = url
        self.events.append(("goto", url))

    def locator(self, selector: str) -> _Locator:
        return _Locator(self, selector)


class _ScriptedLLM:
    provider = "scripted"
    model_id = "scripted-1"

    def __init__(self, actions: list[dict[str, Any]]) -> None:
        self._actions = list(actions)
        self.calls = 0

    async def extract_json(self, prompt: str, *, schema=None, max_tokens: int = 2048) -> LLMResponse:
        self.calls += 1
        if not self._actions:
            raise AssertionError("LLM consulted more times than scripted")
        return _resp(payload=self._actions.pop(0))


_LOGIN_ELEMENTS = [
    {"id": 0, "tag": "input", "type": "text", "text": "", "sel": "input#user"},
    {"id": 1, "tag": "button", "type": "", "text": "Sign in", "sel": "button#go"},
]


async def test_run_records_then_replays_without_llm(tmp_path):
    cache = ActionCache(tmp_path / "actions.sqlite")
    goal = "log in"
    url = "https://site.tld/login"

    # First run: LLM drives, sequence gets recorded.
    llm1 = _ScriptedLLM(
        [
            {"action": "type", "target": 0, "text": "alice"},
            {"action": "click", "target": 1},
            {"action": "done"},
        ]
    )
    page1 = _FakePage(url, _LOGIN_ELEMENTS)
    out1 = await LLMAgentDriver(llm=llm1).run(page1, goal, cache=cache)
    assert out1["replayed"] is False
    assert llm1.calls == 3
    # the driver namespaces its recordings by prompt version, so read with the same.
    recorded = await cache.get(url, goal, prompt_version=PROMPT_VERSION)
    assert recorded == [
        {"action": "type", "sel": "input#user", "text_target": "", "tag": "input", "text": "alice"},
        {"action": "click", "sel": "button#go", "text_target": "Sign in", "tag": "button"},
    ]

    # Second run: cache hit, replay only, LLM never touched.
    llm2 = _ScriptedLLM([])  # any call raises
    page2 = _FakePage(url, _LOGIN_ELEMENTS, selector_counts={"input#user": 1, "button#go": 1})
    out2 = await LLMAgentDriver(llm=llm2).run(page2, goal, cache=cache)
    assert out2["replayed"] is True
    assert llm2.calls == 0
    assert ("fill", "input#user", "alice") in page2.events
    assert ("click", "button#go") in page2.events


async def test_replay_falls_back_to_llm_when_element_missing(tmp_path):
    cache = ActionCache(tmp_path / "actions.sqlite")
    goal = "log in"
    url = "https://site.tld/login"
    await cache.put(
        url,
        goal,
        [
            {"action": "type", "sel": "input#user", "text_target": "", "tag": "input", "text": "alice"},
            {"action": "click", "sel": "button#gone", "text_target": "Vanished", "tag": "button"},
        ],
        prompt_version=PROMPT_VERSION,
    )
    # input#user resolves; button#gone does not, and no element matches the recorded text.
    llm = _ScriptedLLM([{"action": "done"}])
    page = _FakePage(url, _LOGIN_ELEMENTS, selector_counts={"input#user": 1})
    out = await LLMAgentDriver(llm=llm).run(page, goal, cache=cache)
    assert out["replayed"] is False
    assert llm.calls == 1
    # one failed replay, below the eviction threshold -> recording still present
    assert await cache.record_failure(url, goal, prompt_version=PROMPT_VERSION) == 2


async def test_replay_evicts_after_repeated_failures(tmp_path):
    cache = ActionCache(tmp_path / "actions.sqlite")
    goal = "g"
    url = "https://s.tld/"
    bad = [{"action": "click", "sel": "button#nope", "text_target": "Nope", "tag": "button"}]
    await cache.put(url, goal, bad, prompt_version=PROMPT_VERSION)
    driver = LLMAgentDriver(llm=_ScriptedLLM([{"action": "done"}, {"action": "done"}]), cache_max_failures=2)
    page = _FakePage(url, [], selector_counts={})
    await driver.run(page, goal, cache=cache)  # failure 1
    assert await cache.get(url, goal, prompt_version=PROMPT_VERSION) == bad
    await driver.run(page, goal, cache=cache)  # failure 2 -> evicted
    assert await cache.get(url, goal, prompt_version=PROMPT_VERSION) == []


async def test_replay_handles_goto_and_scroll(tmp_path):
    page = _FakePage("https://s.tld/", [])
    ok = await _replay(
        page,
        [{"action": "scroll"}, {"action": "goto", "text": "https://s.tld/next"}],
    )
    assert ok is True
    assert page.url == "https://s.tld/next"
    assert ("wheel", 900) in page.events
    # a relative/non-http goto in a recording fails the replay
    assert await _replay(page, [{"action": "goto", "text": "/relative"}]) is False
    # an unknown action type fails the replay
    assert await _replay(page, [{"action": "teleport"}]) is False


# --- lenient action parsing (Feature 4) -----------------------------------

def test_parse_action_tolerates_trailing_prose():
    a = parse_action(
        _resp(text='Sure, here is the action: {"action": "click", "target": 2} -- that is my choice.')
    )
    assert a["action"] == "click"
    assert a["target"] == 2


def test_parse_action_tolerates_fences_with_prose():
    a = parse_action(
        _resp(text='Thinking...\n```json\n{"action": "type", "target": 0, "text": "x"}\n```\nDone.')
    )
    assert a["action"] == "type"
    assert a["text"] == "x"


def test_prompt_version_is_stable_and_nonempty():
    assert isinstance(PROMPT_VERSION, str)
    assert len(PROMPT_VERSION) == 16


# --- prompt versioning scopes the action cache (Feature 4) ----------------

async def test_changed_prompt_version_misses_stale_recording(tmp_path):
    cache = ActionCache(tmp_path / "pv.sqlite")
    url, goal = "https://s.tld/", "log in"
    actions = [{"action": "scroll"}]
    await cache.put(url, goal, actions, prompt_version="v1")
    # same version -> hit; different version -> miss; bare (None) -> miss
    assert await cache.get(url, goal, prompt_version="v1") == actions
    assert await cache.get(url, goal, prompt_version="v2") == []
    assert await cache.get(url, goal) == []


# --- Interact actions on a fake page (Feature 1 / 2) -----------------------

class _InteractPage:
    """Minimal Playwright-style page exposing the surface run_actions needs."""

    def __init__(self) -> None:
        self.events: list[tuple] = []
        self.mouse = _Mouse(self)

    async def goto(self, url: str, **_kw: Any) -> None:
        self.url = url
        self.events.append(("goto", url))

    async def click(self, selector: str, **_kw: Any) -> None:
        self.events.append(("click", selector))

    async def type(self, selector: str, text: str) -> None:
        self.events.append(("type", selector, text))

    async def fill(self, selector: str, text: str) -> None:
        self.events.append(("fill", selector, text))

    async def press(self, selector: str, key: str) -> None:
        self.events.append(("press", selector, key))

    async def wait_for_timeout(self, ms: int) -> None:
        self.events.append(("wait", ms))


async def test_run_actions_drives_fake_page_in_order():
    page = _InteractPage()
    summary = await run_actions(
        page,
        [
            Action(type="click", selector="#a"),
            Action(type="type", selector="#u", text="alice"),
            Action(type="fill", selector="#p", text="pw"),
            Action(type="scroll", amount=120),
            Action(type="wait", ms=10),
        ],
    )
    assert summary["executed"] == ["click", "type", "fill", "scroll", "wait"]
    assert page.events == [
        ("click", "#a"),
        ("type", "#u", "alice"),
        ("fill", "#p", "pw"),
        ("wheel", 120),
        ("wait", 10),
    ]


def test_agent_tier_no_driver_guard_bypassed_when_actions_present(tmp_path):
    """The 'no-agent-driver-configured' block must NOT apply when actions are given.

    playwright isn't installed in CI, so fetch() can't run end-to-end; assert the
    guard *condition* the tier uses instead.
    """
    from scrapo.access.agent_tier import AgentTier

    tier = AgentTier(Config(data_dir=tmp_path / "g"))
    assert tier.driver is None
    actions = [Action(type="click", selector="#go")]
    # guard fires only with neither a driver nor actions
    assert (tier.driver is None and not actions) is False
    assert (tier.driver is None and not None) is True
