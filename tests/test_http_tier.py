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


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_follows_safe_redirect(cfg):
    # A public -> public redirect must still be followed and the body returned,
    # with final_url reflecting the last hop.
    respx.get("https://start.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "https://end.example.com/page"})
    )
    respx.get("https://end.example.com/page").mock(
        return_value=httpx.Response(200, text="<html>landed</html>")
    )
    result = await HttpTier(cfg).fetch("https://start.example.com/", tier=Tier.HTTP)
    assert result.status == 200
    assert "landed" in result.html
    assert result.final_url == "https://end.example.com/page"
    assert not result.blocked


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_blocks_redirect_to_metadata_endpoint(cfg):
    # SSRF: an allowed public host 302-redirects to the cloud metadata IP.
    # The guard must re-check the redirect target and refuse to follow it.
    redirect = respx.get("https://evil.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data/"})
    )
    metadata = respx.get("http://169.254.169.254/latest/meta-data/").mock(
        return_value=httpx.Response(200, text="secrets")
    )
    result = await HttpTier(cfg).fetch("https://evil.example.com/", tier=Tier.HTTP)
    assert result.blocked is True
    assert result.block_reason.startswith("ssrf-redirect:")
    assert "169.254.169.254" in result.block_reason
    assert redirect.called
    # The dangerous final hop must NEVER have been requested.
    assert not metadata.called


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_blocks_redirect_to_loopback(cfg):
    redirect = respx.get("https://pub.example.com/").mock(
        return_value=httpx.Response(301, headers={"Location": "http://127.0.0.1:6379/"})
    )
    internal = respx.get("http://127.0.0.1:6379/").mock(return_value=httpx.Response(200, text="redis"))
    result = await HttpTier(cfg).fetch("https://pub.example.com/", tier=Tier.HTTP)
    assert result.blocked is True
    assert result.block_reason.startswith("ssrf-redirect:")
    assert redirect.called
    assert not internal.called


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_allow_private_follows_redirect_to_loopback(tmp_path):
    cfg = Config(data_dir=tmp_path / "scrapo", request_timeout=2.0, allow_private_hosts=True)
    respx.get("https://pub.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://127.0.0.1:9000/x"})
    )
    respx.get("http://127.0.0.1:9000/x").mock(return_value=httpx.Response(200, text="<html>ok</html>"))
    result = await HttpTier(cfg).fetch("https://pub.example.com/", tier=Tier.HTTP)
    assert result.status == 200
    assert result.final_url == "http://127.0.0.1:9000/x"


@pytest.mark.asyncio
@respx.mock
async def test_http_tier_reuses_client_across_fetches(cfg):
    respx.get("https://a.example.com/").mock(return_value=httpx.Response(200, text="<html>a</html>"))
    respx.get("https://b.example.com/").mock(return_value=httpx.Response(200, text="<html>b</html>"))
    tier = HttpTier(cfg)
    await tier.fetch("https://a.example.com/", tier=Tier.HTTP)
    await tier.fetch("https://b.example.com/", tier=Tier.HTTP)
    # No proxy -> exactly one shared, still-open client reused across both fetches.
    assert len(tier._clients) == 1
    client = tier._clients[None]
    assert client.is_closed is False
    await tier.aclose()
    assert client.is_closed is True
    assert tier._clients == {}
