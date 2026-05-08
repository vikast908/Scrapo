"""Failure-signal detection — when to escalate to a more expensive tier."""

from __future__ import annotations

import re

from scrapo.types import FetchResult

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


def detect_block(html: str, status: int) -> tuple[bool, str | None]:
    """Return (blocked, reason) — works on a small HTML window for speed."""
    if status in (401, 403, 407, 429):
        return True, f"http-{status}"
    if status == 503:
        return True, "http-503"
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


def annotate(result: FetchResult) -> FetchResult:
    blocked, reason = detect_block(result.html, result.status)
    result.blocked = blocked
    result.block_reason = reason
    return result
