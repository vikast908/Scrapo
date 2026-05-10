"""Failure-signal detection: when to escalate to a more expensive tier."""

from __future__ import annotations

import re

from scrapo.types import FetchResult

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script[\s>]", re.I)
_SPA_ROOT_RE = re.compile(
    r'id=["\'](root|app|__next|__nuxt|svelte|q-app)["\']|data-reactroot|ng-app', re.I
)

_BLOCK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"cf-browser-verification|cf-chl-bypass|__cf_chl_", re.I), "cloudflare"),
    (re.compile(r"akamai.*reference|ak-bm-", re.I), "akamai"),
    (re.compile(r"perimeterx|_pxhd|px-captcha", re.I), "perimeterx"),
    (re.compile(r"datadome", re.I), "datadome"),
    (re.compile(r"distil.*captcha|distil_r", re.I), "distil"),
    (re.compile(r"please enable javascript", re.I), "js-required"),
    (re.compile(r"access denied|you have been blocked|blocked by", re.I), "generic-block"),
    (re.compile(r"hCaptcha|recaptcha", re.I), "captcha"),
    (re.compile(r"are you a human|prove you are not a robot", re.I), "human-check"),
]


def detect_block(html: str, status: int, *, has_body: bool = True) -> tuple[bool, str | None]:
    """Return (blocked, reason). Works on a small HTML window for speed.

    ``has_body=False`` (non-text content like a PDF, carried out of band) skips the
    empty-body and HTML-fingerprint checks; only the HTTP status still counts.
    """
    if status in (401, 403, 407, 429):
        return True, f"http-{status}"
    if status == 503:
        return True, "http-503"
    if not has_body:
        return False, None
    sample = html[:8192] if len(html) > 8192 else html
    if not sample.strip():
        return True, "empty-body"
    for pattern, label in _BLOCK_PATTERNS:
        if pattern.search(sample):
            return True, label
    return False, None


def is_thin(html: str, min_chars: int = 200) -> bool:
    """Body too small to plausibly contain meaningful content."""
    return len(html.strip()) < min_chars


def _visible_text_len(html: str) -> int:
    no_scripts = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html, flags=re.I | re.S)
    return len(" ".join(_TAG_RE.sub(" ", no_scripts).split()))


def is_spa_shell(html: str, *, min_html: int = 1500, max_visible: int = 220) -> bool:
    """Heuristic: lots of markup and script, almost no rendered text yet.

    Catches the classic single-page-app shell (an empty ``<div id="root">`` plus
    a bundle of ``<script>`` tags) so the router escalates straight to a browser
    instead of wasting an HTTP attempt and then mislabeling the result "thin".
    """
    if len(html) < min_html:
        return False
    visible = _visible_text_len(html)
    if visible >= max_visible:
        return False
    scripts = len(_SCRIPT_RE.findall(html))
    return scripts >= 3 or bool(_SPA_ROOT_RE.search(html))


def annotate(result: FetchResult) -> FetchResult:
    blocked, reason = detect_block(
        result.html, result.status, has_body=result.raw_content is None
    )
    result.blocked = blocked
    result.block_reason = reason
    return result
