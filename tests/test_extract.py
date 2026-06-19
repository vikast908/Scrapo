
import pytest
from pydantic import BaseModel

from scrapo.extract.hybrid import HybridExtractor
from scrapo.extract.llm_adapters.base import LLMResponse
from scrapo.extract.pinning import PinnedModel, PinViolation
from scrapo.extract.schema import schema_hash, schema_version
from scrapo.extract.selector_cache import SelectorCache


class Product(BaseModel):
    name: str
    price: str | None = None


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


async def test_subdomains_do_not_share_selectors(cache):
    sh = schema_hash(Product)
    await cache.put("https://shop.example.com/p", sh, {"name": {"selector": "h1", "type": "css"}})
    assert "name" in await cache.get("https://shop.example.com/p", sh)
    assert await cache.get("https://blog.example.com/p", sh) == {}


async def test_stale_selectors_evicted_after_repeated_failures(cache):
    from scrapo.extract.hybrid import STALE_SELECTOR_THRESHOLD

    sh = schema_hash(Product)
    await cache.put("https://e.com/p", sh, {"name": {"selector": ".does-not-exist", "type": "css"}})
    bad_html = "<html><body><h1>Unrelated</h1></body></html>"
    extractor = HybridExtractor(cache, llm=FakeLLM('{"name": "from-llm"}'))

    for _ in range(STALE_SELECTOR_THRESHOLD):
        result = await extractor.extract(
            url="https://e.com/p", html=bad_html, markdown="x", model=Product
        )
        assert result.method == "llm"

    assert await cache.get("https://e.com/p", sh) == {}


async def test_extract_respects_llm_budget(cache):
    from scrapo.types import Budget

    llm = FakeLLM('{"name": "x"}')
    result = await HybridExtractor(cache, llm=llm).extract(
        url="https://e.com/p",
        html="<html></html>",
        markdown="x",
        model=Product,
        budget=Budget(max_llm_calls=0),
    )
    assert result.method == "none"
    assert llm.calls == 0


async def test_budget_max_llm_calls_blocks_second_call(cache):
    from scrapo.types import Budget

    class CostLLM:
        provider = "x"
        model_id = "x-1"

        def __init__(self):
            self.calls = 0

        async def extract_json(self, prompt, *, schema=None, max_tokens=2048):
            self.calls += 1
            return LLMResponse(
                text='{"name": "y"}',
                json_payload={"name": "y"},
                provider="x",
                model_id="x-1",
                cost_usd=0.001,
            )

    llm = CostLLM()
    extractor = HybridExtractor(cache, llm=llm)
    budget = Budget(max_llm_calls=1)

    # First page (different template so no cache hit) uses the one allowed call.
    first = await extractor.extract(
        url="https://e.com/a/1", html="<html></html>", markdown="x", model=Product, budget=budget
    )
    assert first.method == "llm"
    assert llm.calls == 1
    assert budget.llm_calls_made == 1

    # Second page must be blocked — budget exhausted, no further LLM call.
    second = await extractor.extract(
        url="https://e.com/b/2", html="<html></html>", markdown="x", model=Product, budget=budget
    )
    assert second.method == "none"
    assert llm.calls == 1


async def test_budget_max_cost_blocks_once_exceeded(cache):
    from scrapo.types import Budget

    class CostLLM:
        provider = "x"
        model_id = "x-1"

        def __init__(self):
            self.calls = 0

        async def extract_json(self, prompt, *, schema=None, max_tokens=2048):
            self.calls += 1
            return LLMResponse(
                text='{"name": "y"}',
                json_payload={"name": "y"},
                provider="x",
                model_id="x-1",
                cost_usd=0.01,
            )

    llm = CostLLM()
    extractor = HybridExtractor(cache, llm=llm)
    budget = Budget(max_cost_usd=0.005)

    # First call is permitted (spent_usd starts at 0), then pushes spend over cap.
    first = await extractor.extract(
        url="https://e.com/a/1", html="<html></html>", markdown="x", model=Product, budget=budget
    )
    assert first.method == "llm"
    assert llm.calls == 1
    assert budget.spent_usd == 0.01

    # Now over the cost cap — next call blocked.
    second = await extractor.extract(
        url="https://e.com/b/2", html="<html></html>", markdown="x", model=Product, budget=budget
    )
    assert second.method == "none"
    assert llm.calls == 1


async def test_different_templates_same_host_get_separate_cache_entries(cache):
    sh = schema_hash(Product)
    await cache.put(
        "https://shop.example.com/product/12345/foo",
        sh,
        {"name": {"selector": "h1.product", "type": "css"}},
    )
    await cache.put(
        "https://shop.example.com/category/widgets",
        sh,
        {"name": {"selector": "h1.category", "type": "css"}},
    )

    # Same template (different id) shares the product entry.
    product_other = await cache.get("https://shop.example.com/product/67890/bar", sh)
    assert product_other["name"]["selector"] == "h1.product"

    # The category template has its own, distinct entry.
    category = await cache.get("https://shop.example.com/category/widgets", sh)
    assert category["name"]["selector"] == "h1.category"

    # Evicting the product template must not touch the category entry.
    await cache.invalidate("https://shop.example.com/product/99999/baz", sh)
    assert await cache.get("https://shop.example.com/product/12345/foo", sh) == {}
    assert (await cache.get("https://shop.example.com/category/widgets", sh))[
        "name"
    ]["selector"] == "h1.category"


async def test_cost_propagates_from_llm_response(cache):
    class CostLLM:
        provider = "x"
        model_id = "x-1"

        async def extract_json(self, prompt, *, schema=None, max_tokens=2048):
            return LLMResponse(
                text='{"name": "y"}',
                json_payload={"name": "y"},
                provider="x",
                model_id="x-1",
                cost_usd=0.0042,
            )

    result = await HybridExtractor(cache, llm=CostLLM()).extract(
        url="https://e.com/p", html="<html></html>", markdown="x", model=Product
    )
    assert result.cost_usd == 0.0042


class Tag(BaseModel):
    label: str


class Catalog(BaseModel):
    title: str
    tags: list[Tag] = []


CATALOG_HTML = """\
<html><head><title>Catalog</title></head><body>
<h1 id="t">My Catalog</h1>
<ul class="tags">
  <li><span class="lbl">alpha</span></li>
  <li><span class="lbl">beta</span></li>
  <li><span class="lbl">gamma</span></li>
</ul>
</body></html>
"""


def test_list_fields_detected():
    from scrapo.extract.schema import list_fields

    assert list_fields(Catalog) == {"tags": Tag}
    assert list_fields(Product) == {}


async def test_list_field_extracted_verified_and_cached(cache):
    payload = (
        '{"title": "My Catalog", "tags": [{"label": "alpha"}, {"label": "beta"}, {"label": "gamma"}]}\n'
        'SELECTORS: {"title": "h1#t", "tags": {"__list__": "ul.tags > li", "label": "span.lbl"}}'
    )
    llm = FakeLLM(payload)
    extractor = HybridExtractor(cache, llm=llm)

    result = await extractor.extract(
        url="https://e.com/cat", html=CATALOG_HTML, markdown="x", model=Catalog
    )
    assert result.method == "llm"
    assert [t.label for t in result.data.tags] == ["alpha", "beta", "gamma"]

    cached = await cache.get("https://e.com/cat", schema_hash(Catalog))
    assert cached["tags"]["type"] == "list"
    assert cached["tags"]["selector"] == "ul.tags > li"
    assert cached["tags"]["extra"]["items"]["label"] == "span.lbl"

    second = await extractor.extract(
        url="https://e.com/cat", html=CATALOG_HTML, markdown="x", model=Catalog
    )
    assert llm.calls == 1
    assert second.method == "selector"
    assert [t.label for t in second.data.tags] == ["alpha", "beta", "gamma"]
    assert second.data.title == "My Catalog"
