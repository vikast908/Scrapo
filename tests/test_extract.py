from typing import Optional

import pytest
from pydantic import BaseModel

from scrapo.extract.hybrid import HybridExtractor
from scrapo.extract.llm_adapters.base import LLMResponse
from scrapo.extract.pinning import PinnedModel, PinViolation
from scrapo.extract.schema import schema_hash, schema_version
from scrapo.extract.selector_cache import SelectorCache


class Product(BaseModel):
    name: str
    price: Optional[str] = None


class FakeLLM:
    provider = "fake"
    model_id = "fake-1"

    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def extract_json(self, prompt, *, schema=None, max_tokens=2048):
        self.calls += 1
        return LLMResponse(
            text=self.payload,
            json_payload=None,
            provider=self.provider,
            model_id=self.model_id,
        )


PAGE_HTML = """\
<html><head><title>Widget</title></head>
<body>
<main>
  <h1 class="product-title">Widget Pro</h1>
  <span class="price-tag">$42</span>
</main>
</body></html>
"""

PAGE_MD = "# Widget Pro\n\n$42\n"


@pytest.fixture
def cache(tmp_path):
    return SelectorCache(tmp_path / "selectors.sqlite")


def test_schema_hash_stable():
    h1 = schema_hash(Product)
    h2 = schema_hash(Product)
    assert h1 == h2
    assert "@" in schema_version(Product)


async def test_llm_path_caches_selectors(cache):
    payload = (
        '{"name": "Widget Pro", "price": "$42"}\n'
        'SELECTORS: {"name": "h1.product-title", "price": ".price-tag"}'
    )
    llm = FakeLLM(payload)
    extractor = HybridExtractor(cache, llm=llm)

    result = await extractor.extract(
        url="https://example.com/widget",
        html=PAGE_HTML,
        markdown=PAGE_MD,
        model=Product,
    )

    assert result.method == "llm"
    assert result.data.name == "Widget Pro"
    assert result.data.price == "$42"
    assert result.selectors_used["name"] == "h1.product-title"

    cached = await cache.get("https://example.com/widget", schema_hash(Product))
    assert "name" in cached
    assert cached["name"]["selector"] == "h1.product-title"


async def test_second_run_uses_selector_cache_no_llm(cache):
    payload = (
        '{"name": "Widget Pro", "price": "$42"}\n'
        'SELECTORS: {"name": "h1.product-title", "price": ".price-tag"}'
    )
    llm = FakeLLM(payload)
    extractor = HybridExtractor(cache, llm=llm)

    await extractor.extract(
        url="https://example.com/widget",
        html=PAGE_HTML,
        markdown=PAGE_MD,
        model=Product,
    )
    assert llm.calls == 1

    second = await extractor.extract(
        url="https://example.com/widget",
        html=PAGE_HTML,
        markdown=PAGE_MD,
        model=Product,
    )
    assert llm.calls == 1
    assert second.method == "selector"
    assert second.data.name == "Widget Pro"


async def test_pin_mismatch_raises(cache):
    pin = PinnedModel(provider="anthropic", model_id="claude-opus-4-7", prompt_template_hash="x" * 64)
    llm = FakeLLM('{"name": "x"}')
    extractor = HybridExtractor(cache, llm=llm, pin=pin)
    with pytest.raises(PinViolation):
        await extractor.extract(
            url="https://example.com/x", html=PAGE_HTML, markdown=PAGE_MD, model=Product
        )
