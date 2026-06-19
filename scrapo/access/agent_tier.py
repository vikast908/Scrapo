"""T4 — Agent-mediated browsing for login walls / multi-step flows.

This is intentionally a thin orchestrator: the heavy lifting (LLM-driven action
selection on top of an accessibility snapshot) plugs in via callbacks so users
can wire Browser Use, Stagehand-style logic, or their own loop.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from scrapo.access.action_cache import ActionCache
from scrapo.access.actions import Action, run_actions
from scrapo.access.signals import annotate
from scrapo.config import Config
from scrapo.types import FetchResult, Tier


class AgentDriver(Protocol):
    """Pluggable agent loop — receives a Playwright page, returns when goal is reached.

    ``cache`` is an optional action recorder/replayer the tier passes in from
    config; a driver that doesn't do action caching can ignore it.
    """

    async def run(
        self, page: Any, goal: str, *, cache: ActionCache | None = None
    ) -> dict[str, Any]: ...


GoalFn = Callable[[str], Awaitable[bool]]


class AgentTier:
    """Tier 4 fetcher — drives a browser through multi-step goals."""

    def __init__(
        self,
        config: Config,
        driver: AgentDriver | None = None,
    ) -> None:
        self.config = config
        self.driver = driver
        self.action_cache: ActionCache | None = (
            ActionCache(config.action_cache_db) if config.agent_action_cache else None
        )

    async def aclose(self) -> None:
        """No persistent resources today; defined so TierRouter.aclose can call it."""
        return

    async def fetch(
        self,
        url: str,
        *,
        goal: str | None = None,
        storage_state: str | None = None,
        actions: list[Action] | None = None,
    ) -> FetchResult:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                html="",
                headers={},
                tier_used=Tier.AGENT,
                blocked=True,
                block_reason=f"playwright-missing:{e}",
            )

        # Interact (explicit actions) is deterministic and needs no LLM, so the
        # tier is usable with actions alone. The "no driver configured" guard only
        # fires for the goal-driven path with neither actions nor a driver.
        if self.driver is None and not actions:
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                html="",
                headers={},
                tier_used=Tier.AGENT,
                blocked=True,
                block_reason="no-agent-driver-configured",
            )

        start = time.perf_counter()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx_kwargs: dict[str, Any] = {"user_agent": self.config.user_agent}
            if storage_state:
                ctx_kwargs["storage_state"] = storage_state
            context = await browser.new_context(**ctx_kwargs)
            page = await context.new_page()
            try:
                await page.goto(url, timeout=self.config.request_timeout * 1000)
                # Deterministic actions run first (no LLM), then the goal-driven loop.
                if actions:
                    await run_actions(
                        page, actions, allow_private=self.config.allow_private_hosts
                    )
                if goal and self.driver is not None:
                    await self.driver.run(page, goal, cache=self.action_cache)
                html = await page.content()
                final_url = page.url
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                return annotate(
                    FetchResult(
                        url=url,
                        final_url=final_url,
                        status=200,
                        html=html,
                        headers={},
                        tier_used=Tier.AGENT,
                        elapsed_ms=elapsed_ms,
                    )
                )
            finally:
                await context.close()
                await browser.close()
