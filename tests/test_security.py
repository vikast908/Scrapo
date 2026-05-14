import pytest

from scrapo.security import SsrfError, check_url, is_url_allowed


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
