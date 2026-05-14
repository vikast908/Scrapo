import pytest

from scrapo.policy.geo import GeoPolicy
from scrapo.policy.pii import PiiClassifier, redact
from scrapo.policy.robots import RobotsGate


def test_pii_email_phone():
    pii = PiiClassifier()
    text = "Reach me at jane.doe@example.com or +1 415-555-1234."
    hits = pii.scan(text)
    kinds = {h.kind for h in hits}
    assert "email" in kinds
    assert "phone" in kinds


def test_pii_credit_card_luhn():
    pii = PiiClassifier()
    valid = "card 4242 4242 4242 4242"
    invalid = "card 1234 5678 9012 3456"
    assert any(h.kind == "credit_card" for h in pii.scan(valid))
    assert not any(h.kind == "credit_card" for h in pii.scan(invalid))


def test_redact_replaces_pii():
    text = "ping me jane.doe@example.com please"
    out = redact(text)
    assert "jane.doe@example.com" not in out
    assert "[REDACTED]" in out


def test_geo_policy_eu_only():
    eu = GeoPolicy.eu_only()
    assert eu.is_allowed("DE")
    assert eu.is_allowed("fr")
    assert not eu.is_allowed("US")
    assert not eu.is_allowed(None)  # require_match=True


def test_geo_policy_open():
    g = GeoPolicy()
    assert g.is_allowed("anything")
    assert g.is_allowed(None)


def test_geo_policy_denied_is_case_insensitive():
    # Caller passes upper-case country codes; the policy must still deny them
    # at check time (regions are normalised to lowercase internally).
    g = GeoPolicy(denied=frozenset({"RU", "CN"}))
    assert not g.is_allowed("ru")
    assert not g.is_allowed("RU")
    assert not g.is_allowed("Cn")
    assert g.is_allowed("us")


def test_geo_policy_allowed_is_case_insensitive():
    g = GeoPolicy(allowed=frozenset({"US", "DE"}), require_match=True)
    assert g.is_allowed("us")
    assert g.is_allowed("DE")
    assert not g.is_allowed("FR")


@pytest.mark.asyncio
async def test_robots_gate_disabled_allows_all():
    gate = RobotsGate("scrapo", enabled=False)
    assert await gate.can_fetch("https://example.com/private")
