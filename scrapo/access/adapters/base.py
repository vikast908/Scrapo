"""Proxy adapter protocol + registry.

Adapters are intentionally tiny: their only job is to translate Scrapo's
request for a proxy in a given geo into vendor-specific URL/credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class ProxyConfig:
    url: str
    region: str | None = None
    sticky_session: str | None = None
    extra_headers: dict[str, str] | None = None


class ProxyAdapter(Protocol):
    name: str

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        """Return a ProxyConfig or None for direct connection."""
        ...


_registry: dict[str, ProxyAdapter] = {}


def register(adapter: ProxyAdapter) -> None:
    _registry[adapter.name] = adapter


def get(name: str) -> ProxyAdapter | None:
    return _registry.get(name)


def list_names() -> list[str]:
    return sorted(_registry.keys())


class registry:
    """Namespace for adapter registry helpers."""

    register = staticmethod(register)
    get = staticmethod(get)
    list_names = staticmethod(list_names)
