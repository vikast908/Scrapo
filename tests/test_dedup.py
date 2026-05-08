from scrapo.crawl.dedup import UrlDeduper, normalize_url


def test_normalize_strips_fragment_and_tracking():
    assert (
        normalize_url("https://Example.com/path?utm_source=x&id=2#frag")
        == "https://example.com/path?id=2"
    )


def test_normalize_default_port():
    assert normalize_url("http://example.com:80/x") == "http://example.com/x"
    assert normalize_url("https://example.com:443/x") == "https://example.com/x"


def test_normalize_sorts_query():
    assert (
        normalize_url("https://example.com/?b=2&a=1")
        == "https://example.com/?a=1&b=2"
    )


def test_url_deduper():
    d = UrlDeduper()
    assert d.add("https://example.com/x")
    assert not d.add("https://EXAMPLE.com/x?utm_x=1")
    assert d.add("https://example.com/y")
    assert "https://example.com/x" in d
