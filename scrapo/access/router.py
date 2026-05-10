"""Tier router — escalate from cheapest viable tier on failure signals."""

from __future__ import annotations

import structlog

from scrapo.access.adapters.base import ProxyAdapter, registry
from scrapo.access.agent_tier import AgentDriver, AgentTier
from scrapo.access.browser_tier import BrowserTier
from scrapo.access.http_tier import HttpTier
from scrapo.access.proxy_pool import ProxyPool
from scrapo.access.signals import is_spa_shell, is_thin
from scrapo.config import Config
from scrapo.types import Budget, FetchResult, Tier

_HTTP_TIERS = (Tier.HTTP, Tier.HTTP_SESSIONED)

log = structlog.get_logger(__name__)


class TierRouter:
    """Picks the cheapest viable tier; escalates on block-signals or thin output."""

    def __init__(
        self,
        config: Config,
        proxy_adapter: ProxyAdapter | None = None,
        agent_driver: AgentDriver | None = None,
    ) -> None:
        self.config = config
        if proxy_adapter is None and config.proxy_adapter:
            proxy_adapter = registry.get(config.proxy_adapter)
        if proxy_adapter is None and config.proxy_urls:
            proxy_adapter = ProxyPool(
                config.proxy_urls, cooldown_seconds=config.proxy_cooldown_seconds
            )
        self.proxy_adapter = proxy_adapter
        if agent_driver is None and config.agent_driver == "llm":
            from scrapo.access.agent_drivers import LLMAgentDriver

            agent_driver = LLMAgentDriver()
        self.http = HttpTier(config, proxy_adapter)
        self.browser = BrowserTier(config, proxy_adapter)
        self.agent = AgentTier(config, agent_driver)

    async def aclose(self) -> None:
        """Release any pooled resources (browser, etc.). Safe to call repeatedly."""
        for tier in (self.http, self.browser, self.agent):
            close = getattr(tier, "aclose", None)
            if close is not None:
                await close()

    async def fetch(
        self,
        url: str,
        *,
        budget: Budget | None = None,
        start_tier: Tier = Tier.HTTP,
        force_tier: Tier | None = None,
        wait_for: str | None = None,
        screenshot: bool = False,
        agent_goal: str | None = None,
        storage_state: str | None = None,
    ) -> FetchResult:
        budget = budget or Budget(max_tier=self.config.default_max_tier)

        if force_tier is not None:
            return await self._fetch_one(
                url,
                force_tier,
                wait_for=wait_for,
                screenshot=screenshot,
                agent_goal=agent_goal,
                storage_state=storage_state,
            )

        last: FetchResult | None = None
        for tier in self._escalation_path(start_tier, budget):
            log.debug("scrapo.fetch.attempt", url=url, tier=tier.label)
            result = await self._fetch_one(
                url,
                tier,
                wait_for=wait_for,
                screenshot=screenshot,
                agent_goal=agent_goal,
                storage_state=storage_state,
            )
            last = result
            if not self._should_escalate(result):
                return result
            log.info(
                "scrapo.fetch.escalate",
                url=url,
                from_tier=tier.label,
                reason=result.block_reason,
                status=result.status,
            )
        assert last is not None
        return last

    @staticmethod
    def _escalation_path(start: Tier, budget: Budget) -> list[Tier]:
        path: list[Tier] = []
        for tier in (
            Tier.HTTP,
            Tier.HTTP_SESSIONED,
            Tier.BROWSER,
            Tier.BROWSER_STEALTH,
            Tier.AGENT,
        ):
            if tier < start:
                continue
            if not budget.can_use_tier(tier):
                break
            path.append(tier)
        return path

    @staticmethod
    def _should_escalate(result: FetchResult) -> bool:
        if result.blocked or result.status >= 400:
            return True
        if result.raw_content is not None:
            return False  # binary payload (PDF, etc.) - "thin"/SPA checks don't apply
        return is_thin(result.html) or (
            result.tier_used in _HTTP_TIERS and is_spa_shell(result.html)
        )

    async def _fetch_one(
        self,
        url: str,
        tier: Tier,
        *,
        wait_for: str | None,
        screenshot: bool,
        agent_goal: str | None,
        storage_state: str | None,
    ) -> FetchResult:
        if tier in (Tier.HTTP, Tier.HTTP_SESSIONED):
            return await self.http.fetch(url, tier=tier)
        if tier in (Tier.BROWSER, Tier.BROWSER_STEALTH):
            return await self.browser.fetch(
                url,
                tier=tier,
                wait_for=wait_for,
                screenshot=screenshot,
                storage_state=storage_state,
            )
        if tier is Tier.AGENT:
            goal = agent_goal or f"navigate to {url} and surface its main content"
            return await self.agent.fetch(url, goal=goal, storage_state=storage_state)
        raise ValueError(f"unhandled tier: {tier}")
