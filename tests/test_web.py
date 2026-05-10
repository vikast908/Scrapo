import pytest

from scrapo.types import Tier
from scrapo.web import _coerce_tier, describe_block_reason, normalize_target, public_result


def test_normalize_target_accepts_domain_and_url():
    assert normalize_target("trinka.ai") == "https://trinka.ai"
    assert normalize_target("https://www.trinka.ai/features") == "https://www.trinka.ai/features"


@pytest.mark.parametrize("value", ["", "ftp://example.com", "https:///missing-host"])
def test_normalize_target_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        normalize_target(value)


def test_public_result_strips_html_and_summarizes_chunks():
    result = public_result(
        {
            "run_id": "abc123",
            "url": "https://example.com/",
            "status": 200,
            "tier_used": "http",
            "title": "Example",
            "markdown": "# Example",
            "html": "<html></html>",
            "chunks": [
                {
                    "text": "Body",
                    "provenance": {
                        "heading_trail": ["Example"],
                        "selector_path": "html > body",
                    },
                }
            ],
        }
    )

    assert "html" not in result
    assert result["chunk_count"] == 1
    assert result["chunks"][0]["heading_trail"] == ["Example"]


def test_public_result_keeps_blocked_url_and_describes_reason():
    result = public_result(
        {
            "run_id": "blocked123",
            "url": "https://www.linkedin.com/",
            "blocked": True,
            "block_reason": "robots",
        }
    )

    assert result["url"] == "https://www.linkedin.com/"
    assert result["blocked"] is True
    assert result["block_reason"] == "Blocked by robots.txt policy."


def test_coerce_tier_uses_default_and_rejects_bad_values():
    assert _coerce_tier(None, Tier.BROWSER) is Tier.BROWSER
    assert _coerce_tier("0", Tier.BROWSER) is Tier.HTTP
    with pytest.raises(ValueError):
        _coerce_tier("9", Tier.BROWSER)


def test_describe_block_reason_formats_known_policy_reasons():
    assert describe_block_reason("robots") == "Blocked by robots.txt policy."
    assert describe_block_reason("geo-policy-violation:US") == "Blocked by geo policy: US."
    assert describe_block_reason("geo-policy-violation:None") == "Blocked by geo policy: unknown region."
    assert describe_block_reason("geo-policy-violation:") == "Blocked by geo policy: unknown region."
