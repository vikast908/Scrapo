"""API-first URL resolution — fetch a site's clean public API instead of its page.

Some sites that aggressively block scrapers publish the *same* content through a
stable, unauthenticated API. When we recognise such a URL we fetch the API
endpoint instead of the human-facing page: faster, already structured, and not
behind the bot wall (Cloudflare / CAPTCHA / etc.) that defeats the tier router.
This is the cheapest possible "tier" — :func:`resolve_api` is consulted *before*
T0 in :func:`scrapo.api.scrape`, and when it matches the whole escalation ladder
is skipped.

Wikipedia is the motivating case: it CAPTCHAs scrapers on every page, yet exposes
every article through the public REST API (``/api/rest_v1/page/html/{title}``),
which returns clean article HTML that flows through the normal markdown / chunk /
extraction pipeline untouched. The same contract is shared by its Wikimedia
sister projects (Wiktionary, Wikinews, …), so they come along for free.

The registry (:data:`_PROVIDERS`) is a plain tuple of ``url -> ApiRequest | None``
matchers — adding a provider is a few lines and never touches the router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlsplit


@dataclass(slots=True)
class ApiRequest:
    """An API endpoint to GET in place of a human-facing URL.

    ``url`` is the endpoint to fetch; ``provider`` names the matcher and is
    surfaced to callers as ``result.via = "api:<provider>"``; ``headers`` are any
    extra request headers the endpoint wants (empty for Wikimedia, which is happy
    with the default User-Agent).
    """

    url: str
    provider: str
    headers: dict[str, str] = field(default_factory=dict)


# Wikimedia families that all expose the same REST contract under {lang}.{family}.org.
_WIKIMEDIA_FAMILIES = frozenset(
    {
        "wikipedia",
        "wiktionary",
        "wikinews",
        "wikibooks",
        "wikiquote",
        "wikiversity",
        "wikivoyage",
        "wikisource",
    }
)

# Title namespaces the REST page/html endpoint does not serve as a static article
# (they are dynamic/generated); leave these to the normal tier router.
_SKIP_NAMESPACES = frozenset({"special", "media"})


def _wiki_title(parts: object) -> str:
    """Pull the article title out of a Wikimedia URL, decoded.

    Handles the canonical ``/wiki/<Title>`` path and the ``/w/index.php?title=``
    query form. The returned title is percent-decoded (so a namespace prefix is
    visible for the skip check); the caller re-encodes it for the API path.
    """
    path = parts.path  # type: ignore[attr-defined]
    if path.startswith("/wiki/"):
        seg = path[len("/wiki/") :]
        return unquote(seg) if seg else ""
    if path == "/w/index.php":
        values = parse_qs(parts.query).get("title")  # type: ignore[attr-defined]
        return values[0] if values else ""
    return ""


def _wikimedia(url: str) -> ApiRequest | None:
    """Map a Wikipedia / Wikimedia article URL to its REST ``page/html`` endpoint."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    labels = (parts.hostname or "").lower().split(".")
    # Collapse a mobile host: en.m.wikipedia.org -> en.wikipedia.org
    if len(labels) == 4 and labels[1] == "m":
        labels = [labels[0], labels[2], labels[3]]
    if len(labels) != 3 or labels[2] != "org":
        return None
    lang, family = labels[0], labels[1]
    if not lang or family not in _WIKIMEDIA_FAMILIES:
        return None

    title = _wiki_title(parts)
    if not title:
        return None
    namespace = title.split(":", 1)[0].lower() if ":" in title else ""
    if namespace in _SKIP_NAMESPACES:
        return None

    # Re-encode the whole title as a single path segment (subpage slashes too).
    endpoint = (
        f"https://{lang}.{family}.org/api/rest_v1/page/html/{quote(title, safe='')}"
    )
    return ApiRequest(url=endpoint, provider=family)


# Ordered registry of providers; first match wins.
_PROVIDERS = (_wikimedia,)


def resolve_api(url: str) -> ApiRequest | None:
    """Return the API request to use in place of ``url``, or ``None`` if no
    provider recognises it. Never raises — a malformed URL just yields ``None``."""
    for provider in _PROVIDERS:
        try:
            request = provider(url)
        except Exception:  # noqa: BLE001 - a bad URL must not break the scrape, just skip the rewrite
            request = None
        if request is not None:
            return request
    return None
