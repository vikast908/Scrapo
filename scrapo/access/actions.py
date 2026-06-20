"""Explicit, deterministic browser actions ("Interact").

This is the no-LLM counterpart to the agent driver: the caller supplies an
ordered list of :class:`Action` steps (click / type / scroll / wait / …) and
:func:`run_actions` replays them against a Playwright-style ``page`` object.

Design notes:

* ``goto`` is the only step that can change which origin the browser talks to, so
  it is the only step routed through the SSRF guard (:func:`scrapo.security.check_url`).
  A blocked ``goto`` is recorded as an error and skipped — the page is never told
  to navigate there.
* Every other step is best-effort: a single failing step (a missing selector, a
  timeout) is caught, recorded in the summary, and the sequence continues. A
  deterministic script is usually a mix of required and optional steps, and one
  optional step failing should not abort the rest.
* ``run_actions`` never raises for a normal step failure; it always returns the
  summary dict.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scrapo.security import SsrfError, check_url

# Known action verbs and, per verb, the fields that must be present.
_KNOWN_TYPES = (
    "goto",
    "click",
    "type",
    "fill",
    "press",
    "scroll",
    "scroll_until",
    "click_until",
    "wait",
    "wait_for_selector",
    "screenshot",
)

# Fields each action carries; used by coerce_actions to reject unknown keys.
_ACTION_FIELDS = frozenset(
    {"type", "selector", "text", "url", "key", "timeout_ms", "ms", "amount", "times"}
)

# How many pixels to scroll when an "amount" is not supplied.
_DEFAULT_SCROLL_PX = 800
# Pixels per round for the auto-pagination scroll (a near-viewport jump that
# reliably triggers infinite-scroll loaders).
_DEFAULT_AUTOSCROLL_PX = 2000
# Default cap on rounds for the bounded *_until verbs.
_DEFAULT_UNTIL_ROUNDS = 10
# Pause after each round so lazily-loaded content can arrive before we re-measure.
_DEFAULT_SETTLE_MS = 400


@dataclass(slots=True)
class Action:
    """One deterministic browser step.

    ``type`` is one of :data:`_KNOWN_TYPES`. The remaining fields are optional and
    only meaningful for some verbs:

    * ``goto`` needs ``url``
    * ``click`` / ``wait_for_selector`` need ``selector``
    * ``type`` / ``fill`` need ``selector`` and ``text``
    * ``press`` needs ``key`` (``selector`` defaults to ``body``)
    * ``wait`` uses ``ms``
    * ``scroll`` uses ``amount`` (pixels; defaults to ``_DEFAULT_SCROLL_PX``)
    * ``scroll_until`` keeps scrolling until the page stops growing (or, with a
      ``selector``, until that selector's match count stops increasing), bounded
      by ``times`` rounds (default ``_DEFAULT_UNTIL_ROUNDS``), ``amount`` pixels
      per round, ``ms`` settle delay between rounds — for infinite scroll.
    * ``click_until`` repeatedly clicks ``selector`` (a "load more" button) until
      it is gone, bounded by ``times`` rounds — for click-to-paginate lists.
    """

    type: str
    selector: str | None = None
    text: str | None = None
    url: str | None = None
    key: str | None = None
    timeout_ms: int = 10000
    ms: int | None = None
    amount: int | None = None
    times: int | None = None  # round cap for the bounded *_until verbs

    def __post_init__(self) -> None:
        if self.type not in _KNOWN_TYPES:
            raise ValueError(f"unknown action type: {self.type!r}")
        if self.type == "goto" and not self.url:
            raise ValueError("action 'goto' requires 'url'")
        if self.type in ("click", "wait_for_selector", "click_until") and not self.selector:
            raise ValueError(f"action {self.type!r} requires 'selector'")
        if self.type in ("type", "fill"):
            if not self.selector:
                raise ValueError(f"action {self.type!r} requires 'selector'")
            if self.text is None:
                raise ValueError(f"action {self.type!r} requires 'text'")
        if self.type == "press" and not self.key:
            raise ValueError("action 'press' requires 'key'")


def coerce_actions(items: list[Action | dict[str, Any]]) -> list[Action]:
    """Validate user-supplied actions, turning plain dicts into :class:`Action`.

    Raises ``ValueError`` on malformed input (not a dict/Action, missing ``type``,
    unknown keys, or a per-type validation failure from ``Action.__post_init__``).
    """
    out: list[Action] = []
    for item in items:
        if isinstance(item, Action):
            out.append(item)
            continue
        if not isinstance(item, dict):
            raise ValueError(f"action must be an Action or dict, got {type(item).__name__}")
        unknown = set(item) - _ACTION_FIELDS
        if unknown:
            raise ValueError(f"unknown action field(s): {sorted(unknown)}")
        if "type" not in item:
            raise ValueError("action dict missing 'type'")
        out.append(Action(**item))
    return out


async def run_actions(
    page: Any,
    actions: list[Action],
    *,
    allow_private: bool = False,
) -> dict[str, Any]:
    """Execute ``actions`` against a Playwright-style ``page``.

    Returns a summary ``{"steps": <count of attempted steps>, "errors": [...],
    "executed": [<type>...]}``. ``executed`` lists the verbs that ran without
    raising (a blocked or failed step is omitted from ``executed`` and appears in
    ``errors`` instead). Never raises for a normal step failure.
    """
    summary: dict[str, Any] = {"steps": 0, "errors": [], "executed": []}
    for action in actions:
        summary["steps"] += 1
        # goto is gated by the SSRF guard *before* navigation; a blocked target is
        # recorded as an error and the step skipped (the page never navigates).
        if action.type == "goto":
            try:
                check_url(action.url or "", allow_private=allow_private)
            except SsrfError as e:
                summary["errors"].append(f"goto blocked: {action.url!r}: {e}")
                continue
        try:
            await _dispatch(page, action)
        except Exception as e:  # noqa: BLE001 - per-step isolation: one bad step must not abort the rest
            summary["errors"].append(f"{action.type}: {e}")
            continue
        summary["executed"].append(action.type)
    return summary


async def _dispatch(page: Any, action: Action) -> None:
    """Map a single :class:`Action` onto the page API. May raise; caller isolates."""
    t = action.type
    if t == "goto":
        await page.goto(action.url, timeout=action.timeout_ms)
    elif t == "click":
        await page.click(action.selector, timeout=action.timeout_ms)
    elif t == "type":
        await page.type(action.selector, action.text)
    elif t == "fill":
        await page.fill(action.selector, action.text)
    elif t == "press":
        await page.press(action.selector or "body", action.key)
    elif t == "scroll":
        # Use mouse.wheel for a real wheel event (consistent with the agent driver,
        # which scrolls the same way).
        await page.mouse.wheel(0, action.amount if action.amount is not None else _DEFAULT_SCROLL_PX)
    elif t == "scroll_until":
        await _scroll_until(page, action)
    elif t == "click_until":
        await _click_until(page, action)
    elif t == "wait":
        await page.wait_for_timeout(action.ms if action.ms is not None else 0)
    elif t == "wait_for_selector":
        await page.wait_for_selector(action.selector, timeout=action.timeout_ms)
    elif t == "screenshot":
        await page.screenshot()


async def _measure(page: Any, selector: str | None) -> int:
    """A monotonic progress signal: matches of ``selector`` if given, else page height."""
    if selector:
        nodes = await page.query_selector_all(selector)
        return len(nodes)
    height = await page.evaluate("() => document.body.scrollHeight")
    try:
        return int(height)
    except (TypeError, ValueError):
        return 0


async def _scroll_until(page: Any, action: Action) -> None:
    """Scroll repeatedly until the page (or ``selector`` count) stops growing.

    Bounded by ``times`` rounds so a genuinely infinite feed can't loop forever.
    Stops early the first round that doesn't add new content.
    """
    rounds = action.times if action.times is not None else _DEFAULT_UNTIL_ROUNDS
    settle = action.ms if action.ms is not None else _DEFAULT_SETTLE_MS
    step = action.amount if action.amount is not None else _DEFAULT_AUTOSCROLL_PX
    previous = -1
    for _ in range(max(1, rounds)):
        await page.mouse.wheel(0, step)
        await page.wait_for_timeout(settle)
        current = await _measure(page, action.selector)
        if current <= previous:
            break  # nothing new loaded — we've reached the end
        previous = current


async def _click_until(page: Any, action: Action) -> None:
    """Click ``selector`` until it disappears (e.g. a "Load more" button), bounded by rounds."""
    rounds = action.times if action.times is not None else _DEFAULT_UNTIL_ROUNDS
    settle = action.ms if action.ms is not None else _DEFAULT_SETTLE_MS
    for _ in range(max(1, rounds)):
        target = await page.query_selector(action.selector)
        if target is None:
            break  # the button is gone — fully paginated
        await page.click(action.selector, timeout=action.timeout_ms)
        await page.wait_for_timeout(settle)
