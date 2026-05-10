"""Geo policy — pin proxy regions for residency-sensitive workloads."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class GeoPolicy:
    """Allow/deny lists for proxy regions."""

    allowed: frozenset[str] | None = None
    denied: frozenset[str] = frozenset()
    require_match: bool = False

    def is_allowed(self, region: str | None) -> bool:
        if region is None:
            return not self.require_match
        region_l = region.lower()
        if region_l in self.denied:
            return False
        if self.allowed is None:
            return True
        return region_l in self.allowed

    @classmethod
    def eu_only(cls) -> GeoPolicy:
        eu = frozenset(
            {
                "at", "be", "bg", "cy", "cz", "de", "dk", "ee", "es", "fi",
                "fr", "gr", "hr", "hu", "ie", "it", "lt", "lu", "lv", "mt",
                "nl", "pl", "pt", "ro", "se", "si", "sk",
            }
        )
        return cls(allowed=eu, require_match=True)
