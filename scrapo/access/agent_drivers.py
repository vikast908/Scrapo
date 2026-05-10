"""A reference agent driver for Tier 4 (LLM-driven browsing).

Deliberately small: snapshot the visible interactive elements, ask the LLM for
one action (click / type / scroll / goto / done), execute it, repeat up to a step
limit. Plug it in via ``TierRouter(config, agent_driver=LLMAgentDriver())`` or
``AgentTier(config, driver=...)``, or set ``SCRAPO_AGENT_DRIVER=llm``. It uses the
standard ``LLMAdapter.extract_json`` interface, so any configured provider works.

Action caching (Stagehand-style): when an :class:`~scrapo.access.action_cache.ActionCache`
is passed to :meth:`LLMAgentDriver.run` (the :class:`~scrapo.access.agent_tier.AgentTier`
does this from config), the first successful run for a (host, goal) records the
ordered actions it took. Later runs replay that script directly — no LLM calls —
and only fall back to the model if a replayed step no longer applies.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from scrapo.access.action_cache import ActionCache
from scrapo.extract.llm_adapters.base import LLMAdapter, LLMResponse, get_default

log = structlog.get_logger(__name__)

_ACTIONS = ("click", "type", "scroll", "goto", "done")
_REPLAYABLE = ("click", "type", "scroll", "goto")

_ACTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": list(_ACTIONS)},
        "target": {"type": ["integer", "null"], "description": "element index, for click/type"},
        "text": {"type": ["string", "null"], "description": "text to type, or URL for goto"},
        "reason": {"type": "string"},
    },
    "required": ["action"],
}

_PROMPT = """You are driving a headless web browser to accomplish a goal.

GOAL: {goal}

CURRENT URL: {url}
PAGE TITLE: {title}

INTERACTIVE ELEMENTS (index: tag "text"):
{elements}

Choose ONE next action. Return ONLY a JSON object:
{{"action": "click"|"type"|"scroll"|"goto"|"done", "target": <element index or null>, "text": <text/URL or null>, "reason": "<short>"}}
Rules: use "type" with both "target" and "text"; use "goto" with "text" as the URL; use "scroll"
to reveal more; use "done" when the goal is met OR you are stuck. Do not use code fences."""

# JS that tags visible interactive elements and returns a compact description list.
# Each entry carries a best-effort durable CSS selector (``sel``) so a recorded
# action can be replayed on a later run when the snapshot indices differ.
_SNAPSHOT_JS = """() => {
  const sel = 'a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick]';
  const cssPath = (el) => {
    if (el.id) return el.tagName.toLowerCase() + '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node.tagName.toLowerCase() !== 'html') {
      if (node.id) { parts.unshift(node.tagName.toLowerCase() + '#' + CSS.escape(node.id)); break; }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const sibs = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (sibs.length > 1) part += ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  };
  const out = [];
  let i = 0;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const st = getComputedStyle(e);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    e.setAttribute('data-scrapo-id', String(i));
    const text = (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
    out.push({ id: i, tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '', text, sel: cssPath(e) });
    i += 1;
    if (i >= 60) break;
  }
  return out;
}"""

_SCROLL_PX = 900
_ACTION_TIMEOUT_MS = 5000


@dataclass
class LLMAgentDriver:
    llm: LLMAdapter | None = None
    max_steps: int = 8
    cache_max_failures: int = 2  # drop a cached script after this many failed replays
    _llm: LLMAdapter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._llm = self.llm or get_default()

    async def run(
        self, page: Any, goal: str, *, cache: ActionCache | None = None
    ) -> dict[str, Any]:
        start_url = getattr(page, "url", "") or ""

        if cache is not None and start_url:
            recorded = await cache.get(start_url, goal)
            if recorded:
                if await _replay(page, recorded):
                    await cache.record_success(start_url, goal)
                    log.debug("scrapo.agent.replayed", goal=goal, steps=len(recorded))
                    return {
                        "goal": goal,
                        "steps": recorded,
                        "final_url": getattr(page, "url", ""),
                        "replayed": True,
                    }
                fails = await cache.record_failure(start_url, goal)
                if fails >= self.cache_max_failures:
                    await cache.invalidate(start_url, goal)
                log.info("scrapo.agent.replay_failed", goal=goal, failures=fails)
                # fall through to the LLM loop from wherever replay left the page

        steps: list[dict[str, Any]] = []
        recorded_actions: list[dict[str, Any]] = []
        for _ in range(self.max_steps):
            elements = await _snapshot_elements(page)
            title = ""
            with contextlib.suppress(Exception):
                title = await page.title()
            prompt = _PROMPT.format(
                goal=goal, url=getattr(page, "url", ""), title=title, elements=_format_elements(elements)
            )
            resp = await self._llm.extract_json(prompt, schema=_ACTION_SCHEMA)
            action = parse_action(resp)
            steps.append(action)
            log.debug("scrapo.agent.step", goal=goal, action=action.get("action"), reason=action.get("reason"))
            if action["action"] == "done":
                break
            await _execute(page, action, elements)
            step = _record_step(action, elements)
            if step is not None:
                recorded_actions.append(step)

        if cache is not None and start_url and recorded_actions:
            await cache.put(start_url, goal, recorded_actions)
        return {"goal": goal, "steps": steps, "final_url": getattr(page, "url", ""), "replayed": False}


async def _snapshot_elements(page: Any) -> list[dict[str, Any]]:
    try:
        result = await page.evaluate(_SNAPSHOT_JS)
        return list(result) if isinstance(result, list) else []
    except Exception:
        return []


def _format_elements(elements: list[dict[str, Any]]) -> str:
    if not elements:
        return "(none found)"
    lines = []
    for e in elements[:40]:
        tag = e.get("tag", "?")
        if e.get("type"):
            tag = f"{tag}[{e['type']}]"
        lines.append(f'{e.get("id")}: {tag} "{e.get("text", "")}"')
    return "\n".join(lines)


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        # ```json\n<content>\n```  ->  the middle piece
        parts = text.split("```", 2)
        text = parts[1] if len(parts) >= 2 else ""
        if text.startswith("json"):
            text = text[4:]
    if text.endswith("```"):
        text = text[: text.rfind("```")]
    return text.strip()


def parse_action(resp: LLMResponse) -> dict[str, Any]:
    """Normalize an LLM response into {action, target, text, reason}; never raises."""
    obj: Any = resp.json_payload if isinstance(resp.json_payload, dict) else None
    if obj is None and resp.text:
        with contextlib.suppress(json.JSONDecodeError):
            obj = json.loads(_strip_fences(resp.text))
    if not isinstance(obj, dict):
        return {"action": "done", "target": None, "text": None, "reason": "unparseable action"}
    act = str(obj.get("action") or "done").strip().lower()
    if act not in _ACTIONS:
        act = "done"
    target = obj.get("target")
    return {
        "action": act,
        "target": target if isinstance(target, int) else None,
        "text": obj.get("text") if isinstance(obj.get("text"), str) else None,
        "reason": str(obj.get("reason") or ""),
    }


def _record_step(action: dict[str, Any], elements: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Turn an executed action into a replayable record, or None if it isn't one."""
    act = action["action"]
    if act == "scroll":
        return {"action": "scroll"}
    if act == "goto":
        url = action.get("text")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            return {"action": "goto", "text": url}
        return None
    idx = action.get("target")
    if act not in ("click", "type") or not isinstance(idx, int) or idx < 0 or idx >= len(elements):
        return None
    e = elements[idx]
    record: dict[str, Any] = {
        "action": act,
        "sel": str(e.get("sel") or ""),
        "text_target": str(e.get("text") or ""),
        "tag": str(e.get("tag") or ""),
    }
    if act == "type":
        record["text"] = str(action.get("text") or "")
    return record


async def _execute(page: Any, action: dict[str, Any], elements: list[dict[str, Any]]) -> None:
    act = action["action"]
    if act == "scroll":
        with contextlib.suppress(Exception):
            await page.mouse.wheel(0, _SCROLL_PX)
        return
    if act == "goto":
        url = action.get("text")
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            with contextlib.suppress(Exception):
                await page.goto(url, wait_until="domcontentloaded")
        return
    idx = action.get("target")
    if not isinstance(idx, int) or idx < 0 or idx >= len(elements):
        return
    selector = f'[data-scrapo-id="{idx}"]'
    if act == "click":
        with contextlib.suppress(Exception):
            await page.click(selector, timeout=_ACTION_TIMEOUT_MS)
    elif act == "type":
        with contextlib.suppress(Exception):
            await page.fill(selector, str(action.get("text") or ""), timeout=_ACTION_TIMEOUT_MS)


async def _replay(page: Any, actions: list[dict[str, Any]]) -> bool:
    """Re-run a recorded action sequence. Returns False on the first step that fails."""
    try:
        for action in actions:
            act = action.get("action")
            if act not in _REPLAYABLE:
                return False
            if act == "scroll":
                await page.mouse.wheel(0, _SCROLL_PX)
                continue
            if act == "goto":
                url = action.get("text")
                if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
                    return False
                await page.goto(url, wait_until="domcontentloaded")
                continue
            locator = await _resolve(page, action)
            if locator is None:
                return False
            if act == "click":
                await locator.click(timeout=_ACTION_TIMEOUT_MS)
            else:  # type
                await locator.fill(str(action.get("text") or ""), timeout=_ACTION_TIMEOUT_MS)
    except Exception:
        return False
    return True


async def _resolve(page: Any, action: dict[str, Any]) -> Any | None:
    """Find the element for a recorded click/type: durable selector, then text match."""
    sel = action.get("sel")
    if isinstance(sel, str) and sel:
        with contextlib.suppress(Exception):
            locator = page.locator(sel)
            if await locator.count() == 1:
                return locator.first
    want_text = str(action.get("text_target") or "").strip()
    want_tag = str(action.get("tag") or "")
    if not want_text:
        return None
    for e in await _snapshot_elements(page):
        if e.get("tag") == want_tag and str(e.get("text") or "").strip() == want_text:
            return page.locator(f'[data-scrapo-id="{e.get("id")}"]')
    return None
