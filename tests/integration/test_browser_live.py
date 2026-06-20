"""End-to-end tests against a real headless Chromium + local fixture server.

Run with::

    pip install -e ".[dev,browser]"
    playwright install chromium
    pytest -m integration
"""

import pytest
from pydantic import BaseModel

from scrapo.api import scrape
from scrapo.types import Tier

pytestmark = pytest.mark.integration


class Product(BaseModel):
    name: str
    price: str | None = None


async def test_browser_renders_javascript(live_server, live_config):
    # The HTTP tier sees only a "loading…" shell; a real browser runs the script.
    res = await scrape(live_server + "/", config=live_config, force_tier=Tier.BROWSER)
    assert not res.blocked
    assert "Rendered Heading" in (res.markdown or "")
    assert "content from javascript" in (res.markdown or "")


async def test_scroll_until_loads_more_items(live_server, live_config):
    res = await scrape(
        live_server + "/infinite",
        config=live_config,
        actions=[{"type": "scroll_until", "selector": ".item", "times": 12, "ms": 80}],
    )
    # Started with a single .item; auto-scroll should have grown the list a lot
    # (well beyond what a couple of manual scrolls to the fixed bottom would yield).
    assert (res.html or "").count('class="item"') >= 15


async def test_click_until_paginates(live_server, live_config):
    res = await scrape(
        live_server + "/infinite",
        config=live_config,
        actions=[{"type": "click_until", "selector": "#more", "ms": 50}],
    )
    assert (res.html or "").count('class="item"') >= 15
    # The button removes itself once paginated out.
    assert 'id="more"' not in (res.html or "")


async def test_metadata_extraction_end_to_end(live_server, live_config):
    # No API key needed: the embedded JSON-LD rung satisfies the schema with no LLM.
    res = await scrape(live_server + "/product", schema=Product, config=live_config)
    assert res.extraction is not None
    assert res.extraction.method == "metadata"
    assert res.extraction.data["name"] == "Integration Widget"
    assert res.extraction.data["price"] == "19.99"
    assert res.cost_usd == 0.0
