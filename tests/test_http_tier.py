import httpx
import pytest
import respx

from scrapo.access.http_tier import HttpTier
from scrapo.config import Config
from scrapo.types import Tier


@pytest.fixture
def cfg(tmp_path):
    return Config(data_dir=tmp_path / "scrapo", request_timeout=2.0, http_retries=2)


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_retries_then_succeeds(cfg):
    route = respx.get("https://flaky.example.com/").mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(503, text="still busy"),
            httpx.Response(200, text="<html><body>ok</body></html>"),
        ]
    )
    result = await HttpTier(cfg).fetch("https://flaky.example.com/", tier=Tier.HTTP)
    assert result.status == 200
    assert route.call_count == 3
    assert not result.blocked


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_gives_up_after_retries(cfg):
    respx.get("https://down.example.com/").mock(return_value=httpx.Response(503, text="nope"))
    result = await HttpTier(cfg).fetch("https://down.example.com/", tier=Tier.HTTP)
    assert result.status == 503
    assert result.blocked  # 503 -> annotated as block


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_retries_on_transport_error(cfg):
    route = respx.get("https://timeout.example.com/").mock(
        side_effect=[httpx.ConnectError("boom"), httpx.Response(200, text="<html>ok</html>")]
    )
    result = await HttpTier(cfg).fetch("https://timeout.example.com/", tier=Tier.HTTP)
    assert result.status == 200
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_no_retry_on_404(cfg):
    route = respx.get("https://missing.example.com/").mock(return_value=httpx.Response(404))
    result = await HttpTier(cfg).fetch("https://missing.example.com/", tier=Tier.HTTP)
    assert result.status == 404
    assert route.call_count == 1
