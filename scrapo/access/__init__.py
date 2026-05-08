"""Multi-tier access layer — picks the cheapest viable fetcher per request."""

from scrapo.access.router import TierRouter

__all__ = ["TierRouter"]
