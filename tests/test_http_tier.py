import httpx
import pytest
import respx

from scrapo.access.http_tier import HttpTier
from scrapo.config import Config
from scrapo.types import Conditional, Tier


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


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_conditional_get_304(cfg):
    route = respx.get("https://cond.example.com/").mock(return_value=httpx.Response(304))
    result = await HttpTier(cfg).fetch(
        "https://cond.example.com/", tier=Tier.HTTP, conditional=Conditional(etag='"v1"')
    )
    assert result.status == 304
    assert result.not_modified is True
    assert result.blocked is False
    assert route.call_count == 1
    assert route.calls.last.request.headers.get("if-none-match") == '"v1"'


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_sends_if_modified_since(cfg):
    route = respx.get("https://lm.example.com/").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
    await HttpTier(cfg).fetch(
        "https://lm.example.com/", tier=Tier.HTTP,
        conditional=Conditional(last_modified="Wed, 21 Oct 2026 07:28:00 GMT"),
    )
    assert route.calls.last.request.headers.get("if-modified-since") == "Wed, 21 Oct 2026 07:28:00 GMT"


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_empty_conditional_sends_no_validator_headers(cfg):
    route = respx.get("https://nc.example.com/").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
    await HttpTier(cfg).fetch("https://nc.example.com/", tier=Tier.HTTP, conditional=Conditional())
    assert "if-none-match" not in route.calls.last.request.headers
    assert "if-modified-since" not in route.calls.last.request.headers
