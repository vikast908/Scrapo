"""Browser pool lifecycle (without launching a real browser).

Launching Chromium is out of scope for the offline suite; these just check that
the pool and the tiers tear down cleanly when nothing was ever launched.
"""

import pytest

from scrapo.access.browser_pool import BrowserPool
from scrapo.access.browser_tier import BrowserTier
from scrapo.access.router import TierRouter
from scrapo.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / "scrapo")


@pytest.mark.asyncio
async def test_pool_aclose_is_safe_when_unused():
    pool = BrowserPool()
    await pool.aclose()
    await pool.aclose()  # idempotent


@pytest.mark.asyncio
async def test_browser_tier_lazy_and_closeable(cfg):
    tier = BrowserTier(cfg)
    assert tier._pool is None
    pool = tier._get_pool()
    assert tier._pool is pool
    await tier.aclose()
    assert tier._pool is None


@pytest.mark.asyncio
async def test_router_aclose_is_safe(cfg):
    router = TierRouter(cfg)
    await router.aclose()
    await router.aclose()
