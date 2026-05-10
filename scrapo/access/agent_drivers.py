"""A reference agent driver for Tier 4 (LLM-driven browsing).

Deliberately small: snapshot the visible interactive elements, ask the LLM for
one action (click / type / scroll / goto / done), execute it, repeat up to a step
limit. Plug it in via ``TierRouter(config, agent_driver=LLMAgentDriver())`` or
``AgentTier(config, driver=...)``, or set ``SCRAPO_AGENT_DRIVER=llm``. It uses the
standard ``LLMAdapter.extract_json`` interface, so any configured provider works.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from scrapo.extract.llm_adapters.base import LLMAdapter, LLMResponse, get_default

log = structlog.get_logger(__name__)

_ACTIONS = ("click", "type", "scroll", "goto", "done")

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
_SNAPSHOT_JS = """() => {
  const sel = 'a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick]';
  const out = [];
  let i = 0;
  for (const e of document.querySelectorAll(sel)) {
    const r = e.getBoundingClientRect();
    if (r.width < 1 || r.height < 1) continue;
    const st = getComputedStyle(e);
    if (st.visibility === 'hidden' || st.display === 'none') continue;
    e.setAttribute('data-scrapo-id', String(i));
    const text = (e.innerText || e.value || e.getAttribute('aria-label') || e.getAttribute('placeholder') || e.getAttribute('name') || '').trim().replace(/\\s+/g, ' ').slice(0, 120);
    out.push({ id: i, tag: e.tagName.toLowerCase(), type: e.getAttribute('type') || '', text });
    i += 1;
    if (i >= 60) break;
  }
  return out;
}"""


@dataclass
class LLMAgentDriver:
    llm: LLMAdapter | None = None
    max_steps: int = 8
    _llm: LLMAdapter = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._llm = self.llm or get_default()

    async def run(self, page: Any, goal: str) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
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
        return {"goal": goal, "steps": steps, "final_url": getattr(page, "url", "")}


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


async def _execute(page: Any, action: dict[str, Any], elements: list[dict[str, Any]]) -> None:
    act = action["action"]
    if act == "scroll":
        with contextlib.suppress(Exception):
            await page.mouse.wheel(0, 900)
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
            await page.click(selector, timeout=5000)
    elif act == "type":
        with contextlib.suppress(Exception):
            await page.fill(selector, str(action.get("text") or ""), timeout=5000)
