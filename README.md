# Scrapo

**AI-native, agent-first web scraping for Python — with deterministic replay.**

Scrapo is the missing piece between four overlapping markets: AI-native ingestion (Firecrawl, Crawl4AI, Jina Reader), agentic browsers (Stagehand, Browser Use), production crawlers (Crawlee, Scrapy), and managed access APIs (Bright Data, Zyte, Scrapfly, Oxylabs). It composes the best of each into a single self-hosted Python library.

> Read the full [competitive analysis](deep-research-report%20%281%29.md) and [design plan](PLAN.md) for the gaps Scrapo closes.

## What makes it different

| | Most tools | Scrapo |
|---|---|---|
| Access strategy | One mode (HTTP, browser, or agent) | **5-tier router** auto-escalates only on real failure signals |
| Extraction | Selectors *or* LLM | **Selector-first, LLM fallback, self-healing**; second run uses zero LLM calls |
| Determinism | Output may drift as model changes | **Model pinning** — strict mode refuses unpinned LLM extraction |
| Provenance | Bag of chunks | Per-chunk URL + selector + heading trail |
| Audit | None | Append-only audit log + replay-safe HTML snapshots |
| Replay | None | `scrapo replay <run_id>` re-extracts from archived HTML |
| Compliance | Operator's job | Built-in robots gate, PII classifier, geo policy |
| Vendors | Locked-in | **BYO** proxy (Bright Data / Oxylabs / Scrapfly / Zyte) and LLM (Claude / OpenAI / Gemini) |

## Install

```bash
pip install scrapo

pip install "scrapo[browser,anthropic,mcp]"

pip install "scrapo[all,dev]"
```

For browser tiers:
```bash
playwright install chromium
```

## Quickstart — single page

```python
import asyncio
import scrapo

async def main():
    result = await scrapo.scrape("https://example.com/")
    print(result["markdown"])
    print("run_id:", result["run_id"])

asyncio.run(main())
```

## Typed extraction (selector cache + LLM fallback)

```python
import asyncio
from pydantic import BaseModel
import scrapo

class Product(BaseModel):
    name: str
    price: str

async def main():
    res = await scrapo.scrape(
        "https://example.com/widget",
        schema=Product,
    )
    print(res["extraction"]["data"])
    print("method:", res["extraction"]["method"])

asyncio.run(main())
```

The first call uses the LLM and caches the selectors it learns. Every later call against the same domain + schema uses the cached selectors and **zero LLM calls** — until the page layout changes, at which point Scrapo falls back to the LLM, re-derives selectors, and self-heals.

## Model pinning — production stability

```python
from scrapo.extract.pinning import PinnedModel

pin = PinnedModel.make(
    provider="anthropic",
    model_id="claude-opus-4-7",
    prompt_template="…your prompt template…",
)

await scrapo.scrape(url, schema=Product, pin=pin, strict_pin=True)
```

`strict_pin=True` makes the extractor refuse to run if the configured LLM does not match the pin — silent model drift cannot happen.

## Crawl

```python
import scrapo

await scrapo.crawl(
    seeds=["https://docs.python.org/3/"],
    max_depth=2,
    same_host_only=True,
)
```

## Replay & diff

```bash
scrapo list                # see recorded runs
scrapo replay <run_id>     # re-extract from archived HTML, no network call
scrapo diff <run_a> <run_b># field-level diff between two runs
scrapo audit               # tail the append-only audit log
```

## CLI

```bash
scrapo scrape https://example.com/                            # markdown to stdout
scrapo scrape https://example.com/ --max-tier 3 --screenshot
scrapo crawl https://docs.python.org/3/ --max-depth 2 --max-pages 100
scrapo list --limit 10
scrapo replay <run_id>
scrapo diff <run_a> <run_b>
scrapo adapters                                                # list registered proxy adapters
scrapo mcp                                                     # MCP server over stdio
```

## Use as an MCP server

Scrapo ships an MCP server exposing `scrapo_scrape`, `scrapo_crawl`, `scrapo_replay`, `scrapo_diff`, `scrapo_list_runs` to any MCP-compatible client (Claude Code, Claude Desktop, etc.).

```bash
pip install "scrapo[mcp]"
scrapo mcp
```

Add to your Claude Desktop config:
```json
{
  "mcpServers": {
    "scrapo": {
      "command": "scrapo",
      "args": ["mcp"]
    }
  }
}
```

## BYO proxy adapter

```python
from scrapo.access.adapters.brightdata import BrightDataAdapter
import scrapo

adapter = BrightDataAdapter()  # reads BRIGHTDATA_USERNAME / BRIGHTDATA_PASSWORD
await scrapo.scrape("https://hard-target.com/", proxy_adapter=adapter)
```

Built-in adapters: `brightdata`, `oxylabs`, `scrapfly`, `zyte`. Implement the `ProxyAdapter` protocol for anything else:

```python
from scrapo.access.adapters.base import ProxyConfig

class MyAdapter:
    name = "my-vendor"
    async def get_proxy(self, geo=None):
        return ProxyConfig(url="http://user:pass@my-proxy:8080", region=geo)
```

## BYO LLM adapter

Built-in: `anthropic` (default), `openai`, `gemini`, plus a deterministic `mock` for offline tests.

```python
from scrapo.extract.llm_adapters.anthropic_adapter import AnthropicAdapter
import scrapo

llm = AnthropicAdapter(model_id="claude-sonnet-4-6")
await scrapo.scrape(url, schema=Product, llm_adapter=llm)
```

## Architecture

Six pillars (see [PLAN.md](PLAN.md) for the full design):

```
scrapo/
├── access/      # Tier router (HTTP → browser → stealth → agent) + proxy adapters
├── extract/     # Hybrid selector + LLM extractor with model pinning
├── shape/       # HTML → Markdown + heading-aware chunker with provenance
├── replay/      # SQLite snapshot store + field-level diff
├── policy/      # robots.txt gate, PII classifier, geo policy, audit log
├── crawl/       # Persistent queue + async scheduler + URL dedup
└── agent/       # MCP server + tool schemas
```

## Configuration

All defaults can be overridden by environment variables:

| Variable | Default | Description |
|---|---|---|
| `SCRAPO_DATA_DIR` | platform user-data dir | Where SQLite, snapshots, audit log live |
| `SCRAPO_USER_AGENT` | `scrapo/0.1` | UA for HTTP and robots |
| `SCRAPO_TIMEOUT` | `30` | Request timeout (seconds) |
| `SCRAPO_CONCURRENCY` | `8` | Crawl concurrency |
| `SCRAPO_RESPECT_ROBOTS` | `1` | `0` to disable robots gate |
| `SCRAPO_PII_FILTER` | `0` | `1` to flag PII in audit log |
| `SCRAPO_PROXY_ADAPTER` | _unset_ | Default registered adapter name |
| `SCRAPO_LLM_ADAPTER` | `anthropic` | Default LLM provider |
| `SCRAPO_LLM_MODEL` | `claude-opus-4-7` | Default model id |
| `SCRAPO_GEO` | _unset_ | Default proxy region |
| `ANTHROPIC_API_KEY` | — | Required for the Claude adapter |
| `OPENAI_API_KEY` | — | Required for the OpenAI adapter |
| `GEMINI_API_KEY` | — | Required for the Gemini adapter |

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The suite is fully offline — no test hits the network or a paid LLM.

## License

MIT
