"""Multi-tier access layer — picks the cheapest viable fetcher per request."""

from scrapo.access.proxy_pool import ProxyPool
from scrapo.access.router import TierRouter

__all__ = ["ProxyPool", "TierRouter"]
