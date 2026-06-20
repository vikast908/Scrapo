"""Embedded structured-data extraction (JSON-LD / OpenGraph / microdata)."""

import pytest
from pydantic import BaseModel

from scrapo.extract.hybrid import HybridExtractor
from scrapo.extract.llm_adapters.base import LLMResponse
from scrapo.extract.metadata import extract_from_metadata
from scrapo.extract.selector_cache import SelectorCache


class Product(BaseModel):
    name: str
    price: str | None = None


class Article(BaseModel):
    headline: str
    author: str | None = None
    date_published: str | None = None


class Ingredient(BaseModel):
    name: str


class Recipe(BaseModel):
    name: str
    ingredients: list[Ingredient] = []


JSONLD_PRODUCT = """\
<html><head>
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product", "name": "Widget Pro",
 "offers": {"@type": "Offer", "price": "42.00", "priceCurrency": "USD"}}
</script>
</head><body><h1>unrelated heading</h1></body></html>
"""

OG_ARTICLE = """\
<html><head>
<meta property="og:title" content="The Headline">
<meta name="author" content="Ada Lovelace">
<meta property="article:published_time" content="2026-01-02">
</head><body></body></html>
"""

MICRODATA_PRODUCT = """\
<html><body>
<div itemscope itemtype="https://schema.org/Product">
  <span itemprop="name">Microthing</span>
  <span itemprop="price" content="9.99">$9.99</span>
</div>
</body></html>
"""

GRAPH_RECIPE = """\
<html><head>
<script type="application/ld+json">
{"@graph": [
  {"@type": "WebPage"},
  {"@type": "Recipe", "name": "Pancakes",
   "ingredients": [{"name": "flour"}, {"name": "milk"}, {"name": "egg"}]}
]}
</script>
</head><body></body></html>
"""


@pytest.fixture
def cache(tmp_path):
    return SelectorCache(tmp_path / "selectors.sqlite")


def test_jsonld_product_resolves_nested_price():
    res = extract_from_metadata(JSONLD_PRODUCT, Product)
    assert res is not None
    assert res.source == "json-ld"
    assert res.data.name == "Widget Pro"
    assert res.data.price == "42.00"  # pulled from offers.price


def test_opengraph_article_maps_aliases():
    res = extract_from_metadata(OG_ARTICLE, Article)
    assert res is not None
    # headline <- og:title, author <- name=author, date_published <- article:published_time
    assert res.data.headline == "The Headline"
    assert res.data.author == "Ada Lovelace"
    assert res.data.date_published == "2026-01-02"


def test_microdata_itemprop():
    res = extract_from_metadata(MICRODATA_PRODUCT, Product)
    assert res is not None
    assert res.data.name == "Microthing"
    assert res.data.price == "9.99"  # content= wins over visible text


def test_jsonld_graph_and_list_field():
    res = extract_from_metadata(GRAPH_RECIPE, Recipe)
    assert res is not None
    assert res.data.name == "Pancakes"
    assert [i.name for i in res.data.ingredients] == ["flour", "milk", "egg"]


class StrictDoc(BaseModel):
    isbn: str  # required, no alias, never present in the structured data below
    name: str | None = None


def test_missing_required_field_returns_none():
    # JSON-LD has only a name; StrictDoc requires `isbn` which has no source.
    html = '<html><head><script type="application/ld+json">{"@type":"Thing","name":"x"}</script></head></html>'
    assert extract_from_metadata(html, StrictDoc) is None


def test_bare_title_is_not_a_source():
    # A plain <title> must NOT satisfy a `name`/`title` field — it's page chrome.
    html = "<html><head><title>Just A Title</title></head><body></body></html>"
    assert extract_from_metadata(html, Product) is None


def test_no_structured_data_returns_none():
    html = "<html><body><h1>Hello</h1><p>nothing structured here</p></body></html>"
    assert extract_from_metadata(html, Product) is None


def test_empty_html_returns_none():
    assert extract_from_metadata("", Product) is None


def test_malformed_jsonld_is_ignored():
    html = '<html><head><script type="application/ld+json">{not valid json</script></head></html>'
    assert extract_from_metadata(html, Product) is None


# --- integration with the hybrid extractor ladder --------------------------


class _CountingLLM:
    provider = "fake"
    model_id = "fake-1"

    def __init__(self) -> None:
        self.calls = 0

    async def extract_json(self, prompt, *, schema=None, max_tokens=2048):
        self.calls += 1
        return LLMResponse(
            text='{"name": "from-llm"}', json_payload={"name": "from-llm"},
            provider=self.provider, model_id=self.model_id,
        )


async def test_metadata_rung_skips_llm(cache):
    llm = _CountingLLM()
    extractor = HybridExtractor(cache, llm=llm)
    res = await extractor.extract(
        url="https://shop.example.com/p/1", html=JSONLD_PRODUCT, markdown="x", model=Product
    )
    assert res.method == "metadata"
    assert res.data.name == "Widget Pro"
    assert llm.calls == 0  # never touched the model


async def test_metadata_disabled_falls_through_to_llm(cache):
    llm = _CountingLLM()
    extractor = HybridExtractor(cache, llm=llm, use_metadata=False)
    res = await extractor.extract(
        url="https://shop.example.com/p/2", html=JSONLD_PRODUCT, markdown="x", model=Product
    )
    assert res.method == "llm"
    assert llm.calls == 1


async def test_no_metadata_still_uses_llm(cache):
    llm = _CountingLLM()
    extractor = HybridExtractor(cache, llm=llm)
    plain = "<html><body><h1>Widget</h1></body></html>"
    res = await extractor.extract(
        url="https://shop.example.com/p/3", html=plain, markdown="x", model=Product
    )
    assert res.method == "llm"
    assert llm.calls == 1
