import httpx
import pytest
import respx

from scrapo.security import SsrfError, check_url, is_url_allowed, safe_get


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://127.0.0.1:6379/",
        "http://localhost/admin",
        "https://localhost.localdomain/",
        "http://[::1]/",
        "http://10.0.0.5/",
        "http://192.168.1.1/router",
        "http://172.16.4.4/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://service.internal/",
        "http://db.local/",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "http:///no-host",
    ],
)
def test_internal_targets_are_blocked(url):
    assert not is_url_allowed(url)
    with pytest.raises(SsrfError):
        check_url(url)


@pytest.mark.parametrize(
    "url",
    ["https://example.com/", "http://example.org/page?x=1", "https://sub.example.com:8443/x"],
)
def test_public_targets_are_allowed(url):
    assert is_url_allowed(url)
    check_url(url)  # should not raise


def test_allow_private_override():
    assert not is_url_allowed("http://10.1.2.3/")
    assert is_url_allowed("http://10.1.2.3/", allow_private=True)
    check_url("http://localhost/", allow_private=True)


@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",        # decimal-encoded 127.0.0.1
        "http://017700000001/",      # octal-encoded 127.0.0.1
        "http://0177.0.0.1/",        # dotted octal
        "http://0x7f.0.0.1/",        # dotted hex
        "http://0x7f000001/",        # single hex word
        "http://127.1/",             # short-form IPv4
    ],
)
def test_obfuscated_ip_encodings_are_blocked(url):
    assert not is_url_allowed(url)
    with pytest.raises(SsrfError):
        check_url(url)


def test_obfuscated_public_ip_still_allowed():
    # 8.8.8.8 in decimal — must still be allowed when not loopback/private.
    assert is_url_allowed("http://134744072/")


@pytest.mark.asyncio
@respx.mock
async def test_safe_get_blocks_redirect_to_private():
    respx.get("https://ok.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
    )
    final = respx.get("http://169.254.169.254/").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(SsrfError):
            await safe_get(client, "https://ok.example.com/", allow_private=False)
    assert not final.called  # dangerous hop never issued


@pytest.mark.asyncio
@respx.mock
async def test_safe_get_follows_public_redirect():
    respx.get("https://ok.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "https://dest.example.com/p"})
    )
    respx.get("https://dest.example.com/p").mock(return_value=httpx.Response(200, text="hi"))
    async with httpx.AsyncClient(follow_redirects=False) as client:
        resp = await safe_get(client, "https://ok.example.com/", allow_private=False)
    assert resp.status_code == 200
    assert resp.text == "hi"


@pytest.mark.asyncio
@respx.mock
async def test_safe_get_caps_redirect_count():
    # A loop of public redirects must eventually be cut off by max_redirects.
    respx.get("https://loop.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "https://loop.example.com/"})
    )
    async with httpx.AsyncClient(follow_redirects=False) as client:
        with pytest.raises(SsrfError):
            await safe_get(client, "https://loop.example.com/", allow_private=False, max_redirects=3)
