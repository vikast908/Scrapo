"""SSRF guard.

scrapo is meant to be embedded in apps and handed to LLM agents (via the MCP
server), so a fetched page can influence which URL gets fetched next. That makes
it an attractive pivot toward internal services and cloud metadata endpoints.

This module rejects obviously-internal targets before any request goes out:
loopback, link-local (which includes 169.254.169.254), private RFC 1918 / ULA
ranges, and a small set of well-known local hostnames. It checks IP literals and
hostnames without performing DNS resolution, so it stays fast and offline; a
domain that *resolves* to an internal IP is not caught here and should be handled
by network policy if that is in your threat model.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin, urlparse

if TYPE_CHECKING:
    import httpx

_LOCAL_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
}

_LOCAL_SUFFIXES = (".localhost", ".local", ".internal", ".intranet", ".lan", ".home.arpa")


class SsrfError(ValueError):
    """Raised when a URL points at an address scraping is not allowed to reach."""


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _parse_inet_part(part: str) -> int | None:
    """Parse one number in inet_aton style: ``0x``-prefix hex, leading-``0`` octal,
    else decimal. Returns ``None`` for anything that isn't a clean integer literal."""
    if not part:
        return None
    p = part.lower()
    try:
        if p.startswith("0x"):
            return int(p, 16)
        if len(p) > 1 and p.startswith("0"):
            return int(p, 8)
        return int(p, 10)
    except ValueError:
        return None


def _numeric_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Recognise IP literals that aren't plain dotted-decimal: decimal integer
    (``2130706433``), hex (``0x7f000001``), short-form (``127.1``), and dotted
    octal/hex parts (``0177.0.0.1``, ``0x7f.0.0.1``). Returns the canonical IP if
    parseable, or ``None`` for real hostnames. Mirrors ``inet_aton(3)`` semantics."""
    # IPv6 literal — urlparse strips the brackets before we see it.
    if ":" in host:
        try:
            return ipaddress.IPv6Address(host)
        except ValueError:
            return None
    # Common case: plain dotted-decimal.
    try:
        return ipaddress.IPv4Address(host)
    except ValueError:
        pass
    parts = host.split(".")
    if not 1 <= len(parts) <= 4 or not all(parts):
        return None
    nums: list[int] = []
    for p in parts:
        n = _parse_inet_part(p)
        if n is None or n < 0:
            return None
        nums.append(n)
    try:
        if len(nums) == 1 and nums[0] < (1 << 32):
            packed = nums[0]
        elif len(nums) == 4 and all(n < 256 for n in nums):
            packed = (nums[0] << 24) | (nums[1] << 16) | (nums[2] << 8) | nums[3]
        elif len(nums) == 3 and nums[0] < 256 and nums[1] < 256 and nums[2] < 65536:
            packed = (nums[0] << 24) | (nums[1] << 16) | nums[2]
        elif len(nums) == 2 and nums[0] < 256 and nums[1] < (1 << 24):
            packed = (nums[0] << 24) | nums[1]
        else:
            return None
        return ipaddress.IPv4Address(packed)
    except (ValueError, OverflowError):
        return None


def check_url(url: str, *, allow_private: bool = False) -> None:
    """Raise SsrfError if ``url`` is not a fetchable public http(s) target."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfError(f"unsupported URL scheme: {parsed.scheme or '(none)'}")
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not host:
        raise SsrfError("URL has no host")
    if allow_private:
        return

    ip = _numeric_ip(host)
    if ip is not None:
        if _ip_is_blocked(ip):
            raise SsrfError(f"refusing to fetch private/internal address: {host}")
        return

    if host in _LOCAL_HOSTNAMES or host.endswith(_LOCAL_SUFFIXES):
        raise SsrfError(f"refusing to fetch local hostname: {host}")


def is_url_allowed(url: str, *, allow_private: bool = False) -> bool:
    try:
        check_url(url, allow_private=allow_private)
        return True
    except SsrfError:
        return False


_REDIRECT_STATUS = (301, 302, 303, 307, 308)


async def safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    allow_private: bool = False,
    max_redirects: int = 10,
    **kwargs: Any,
) -> httpx.Response:
    """GET ``url`` following redirects manually, validating EVERY hop with the SSRF
    guard before it is followed.

    The httpx client passed in MUST have ``follow_redirects=False`` (otherwise httpx
    would follow redirects internally, bypassing the per-hop SSRF check). The initial
    ``url`` is validated by the caller's normal flow, but we re-validate it here too so
    this helper is safe on its own.

    Raises :class:`SsrfError` if any hop (including a redirect target) points at a
    blocked address, or if ``max_redirects`` is exceeded.
    """
    check_url(url, allow_private=allow_private)
    current = url
    for _ in range(max_redirects + 1):
        resp = await client.get(current, **kwargs)
        if resp.status_code not in _REDIRECT_STATUS:
            return resp
        location = resp.headers.get("location")
        if not location:
            return resp
        next_url = urljoin(current, location)
        check_url(next_url, allow_private=allow_private)
        # 303 always switches to GET; 307/308 preserve method. Everything here is
        # already a GET, so we simply re-issue a GET on the resolved target.
        current = next_url
    raise SsrfError(f"too many redirects (>{max_redirects}) starting from {url}")
