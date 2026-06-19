import pytest

from scrapo.access.adapters.brightdata import BrightDataAdapter
from scrapo.access.adapters.oxylabs import OxylabsAdapter
from scrapo.access.adapters.scrapfly import ScrapflyAdapter
from scrapo.access.adapters.zyte import ZyteAdapter


@pytest.mark.asyncio
async def test_brightdata_returns_none_without_creds():
    a = BrightDataAdapter(username="", password="")
    assert await a.get_proxy() is None


@pytest.mark.asyncio
async def test_brightdata_encodes_geo():
    a = BrightDataAdapter(username="user", password="pw", host="brd.example.com:22225")
    cfg = await a.get_proxy(geo="US")
    assert cfg is not None
    assert "country-us" in cfg.url
    assert cfg.region == "US"


@pytest.mark.asyncio
async def test_oxylabs_encodes_customer():
    a = OxylabsAdapter(username="user", password="pw")
    cfg = await a.get_proxy(geo="DE")
    assert cfg is not None
    assert "customer-user-cc-de" in cfg.url


@pytest.mark.asyncio
async def test_scrapfly_uses_api_key():
    a = ScrapflyAdapter(api_key="key")
    cfg = await a.get_proxy(geo="GB")
    assert cfg is not None
    assert "scrapfly-country-gb" in cfg.url


@pytest.mark.asyncio
async def test_zyte_sets_geo_header():
    a = ZyteAdapter(api_key="key")
    cfg = await a.get_proxy(geo="FR")
    assert cfg is not None
    assert cfg.extra_headers and cfg.extra_headers["Zyte-Geolocation"] == "FR"


# --- LLM adapter cost accounting (no SDK / network needed) ---


def test_anthropic_cost_includes_cache_tokens():
    from scrapo.extract.llm_adapters.anthropic_adapter import AnthropicAdapter

    # Call the cost helper without constructing the client (which needs the SDK).
    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.model_id = "claude-sonnet-4-6"  # rates: (3.0, 15.0) per MTok

    base = adapter._cost(1_000_000, 0)
    assert base == pytest.approx(3.0)

    # Cache-creation billed ~1.25x input rate, cache-read ~0.10x input rate.
    with_cache = adapter._cost(1_000_000, 0, 1_000_000, 1_000_000)
    assert with_cache == pytest.approx(3.0 + 3.0 * 1.25 + 3.0 * 0.10)
    assert with_cache > base


def test_anthropic_unknown_model_cost_zero():
    from scrapo.extract.llm_adapters.anthropic_adapter import AnthropicAdapter

    adapter = AnthropicAdapter.__new__(AnthropicAdapter)
    adapter.model_id = "no-such-model"
    assert adapter._cost(1_000_000, 1_000_000, 1_000_000, 1_000_000) == 0.0


def test_openai_cost_from_pricing_table_and_fallback():
    from scrapo.extract.llm_adapters.openai_adapter import OpenAIAdapter

    adapter = OpenAIAdapter.__new__(OpenAIAdapter)
    adapter.model_id = "gpt-4o-mini"  # (0.15, 0.60)
    assert adapter._cost(1_000_000, 1_000_000) == pytest.approx(0.15 + 0.60)

    # Unknown model falls back to a non-zero rate so cost is never silently 0.
    adapter.model_id = "totally-unknown"
    assert adapter._cost(1_000_000, 0) > 0.0


def test_gemini_cost_from_pricing_table_and_fallback():
    from scrapo.extract.llm_adapters.gemini_adapter import GeminiAdapter

    adapter = GeminiAdapter.__new__(GeminiAdapter)
    adapter.model_id = "gemini-2.5-flash"  # (0.30, 2.50)
    assert adapter._cost(1_000_000, 1_000_000) == pytest.approx(0.30 + 2.50)

    adapter.model_id = "totally-unknown"
    assert adapter._cost(1_000_000, 0) > 0.0
