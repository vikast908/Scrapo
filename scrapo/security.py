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
from urllib.parse import urlparse

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

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
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
