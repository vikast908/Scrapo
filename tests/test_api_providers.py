"""URL -> public-API resolution (api-first)."""

from __future__ import annotations

import pytest

from scrapo.access.api_providers import resolve_api


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://en.wikipedia.org/wiki/Albert_Einstein",
            "https://en.wikipedia.org/api/rest_v1/page/html/Albert_Einstein",
        ),
        # language subdomain is preserved
        (
            "https://de.wikipedia.org/wiki/Physik",
            "https://de.wikipedia.org/api/rest_v1/page/html/Physik",
        ),
        # mobile host collapses to the canonical one
        (
            "https://en.m.wikipedia.org/wiki/Photon",
            "https://en.wikipedia.org/api/rest_v1/page/html/Photon",
        ),
        # ?title= query form
        (
            "https://en.wikipedia.org/w/index.php?title=Quantum_mechanics",
            "https://en.wikipedia.org/api/rest_v1/page/html/Quantum_mechanics",
        ),
        # sister project (Wiktionary) — provider name follows the family
        (
            "https://en.wiktionary.org/wiki/photon",
            "https://en.wiktionary.org/api/rest_v1/page/html/photon",
        ),
    ],
)
def test_wikimedia_urls_resolve_to_rest_api(url, expected):
    req = resolve_api(url)
    assert req is not None
    assert req.url == expected


def test_provider_name_is_the_family():
    assert resolve_api("https://en.wikipedia.org/wiki/Cat").provider == "wikipedia"
    assert resolve_api("https://en.wiktionary.org/wiki/cat").provider == "wiktionary"


def test_fragment_and_namespaced_title():
    # a fragment is dropped; a servable namespace (Category:) is kept
    req = resolve_api("https://en.wikipedia.org/wiki/Category:Physics#top")
    assert req is not None
    assert req.url == "https://en.wikipedia.org/api/rest_v1/page/html/Category%3APhysics"


def test_encoded_title_is_normalised_not_double_encoded():
    # the incoming %20 is decoded then re-encoded once — never %2520
    req = resolve_api("https://en.wikipedia.org/wiki/New%20York")
    assert req is not None
    assert req.url == "https://en.wikipedia.org/api/rest_v1/page/html/New%20York"


def test_subpage_slash_is_encoded():
    req = resolve_api("https://en.wikisource.org/wiki/Book/Chapter_1")
    assert req is not None
    assert req.url == "https://en.wikisource.org/api/rest_v1/page/html/Book%2FChapter_1"


@pytest.mark.parametrize(
    "url",
    [
        # dynamic namespaces are left to the tier router
        "https://en.wikipedia.org/wiki/Special:Random",
        "https://en.wikipedia.org/wiki/Special:Search",
        # not an article URL
        "https://en.wikipedia.org/wiki/",
        "https://en.wikipedia.org/",
        "https://www.wikipedia.org/",
        # the API endpoint itself must not be rewritten again
        "https://en.wikipedia.org/api/rest_v1/page/html/Cat",
        # not a recognised site
        "https://example.com/wiki/Albert_Einstein",
        "https://github.com/anthropics/scrapo",
        # look-alike host that isn't a Wikimedia family
        "https://en.notwikipedia.org/wiki/Cat",
        # not http(s)
        "ftp://en.wikipedia.org/wiki/Cat",
    ],
)
def test_non_matching_urls_return_none(url):
    assert resolve_api(url) is None


def test_malformed_url_does_not_raise():
    assert resolve_api("not a url") is None
    assert resolve_api("") is None
