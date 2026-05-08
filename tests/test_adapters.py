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
