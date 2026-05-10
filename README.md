<div align="center">

# 🕸️ Scrapo

**The web-scraping library agents deserve.**

*Selector-cheap. LLM-resilient. Replay-safe. Self-hosted.*

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=flat-square)](LICENSE)
[![Status](https://img.shields.io/badge/status-alpha-f59e0b?style=flat-square)](https://github.com/vikast908/Scrapo)
[![Type-checked](https://img.shields.io/badge/type--checked-mypy_strict-3b82f6?style=flat-square)](https://mypy.readthedocs.io/)
[![Lint](https://img.shields.io/badge/lint-ruff-d8b4fe?style=flat-square)](https://github.com/astral-sh/ruff)
[![Async](https://img.shields.io/badge/async-asyncio-2dd4bf?style=flat-square)](https://docs.python.org/3/library/asyncio.html)

[![Playwright](https://img.shields.io/badge/browser-Playwright-2EAD33?style=flat-square&logo=playwright)](https://playwright.dev)
[![Pydantic](https://img.shields.io/badge/schema-Pydantic_v2-E92063?style=flat-square&logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![Anthropic](https://img.shields.io/badge/LLM-Anthropic-D97706?style=flat-square)](https://www.anthropic.com/)
[![OpenAI](https://img.shields.io/badge/LLM-OpenAI-412991?style=flat-square&logo=openai)](https://platform.openai.com/)
[![Gemini](https://img.shields.io/badge/LLM-Gemini-4285F4?style=flat-square&logo=google)](https://ai.google.dev/)
[![MCP](https://img.shields.io/badge/protocol-MCP-7c3aed?style=flat-square)](https://modelcontextprotocol.io)

[Quickstart](#quickstart) • [Architecture](#architecture) • [Features](#features) • [Why Scrapo](#why-scrapo) • [CLI](#cli) • [MCP](#use-as-an-mcp-server)

</div>

---

## What is Scrapo?

Scrapo is a Python library that fuses four worlds the rest of the market keeps separate:

<table>
<tr>
<td align="center" width="25%">

**AI-native ingestion**<br>*markdown, schema JSON*

</td>
<td align="center" width="25%">

**Agentic browsing**<br>*observe / act / extract*

</td>
<td align="center" width="25%">

**Production crawling**<br>*queues, dedup, scaling*

</td>
<td align="center" width="25%">

**Managed access**<br>*proxies, anti-bot, geo*

</td>
</tr>
</table>

…with a feature nobody else ships: **deterministic replay** of every fetch, so extraction drift is auditable.

> Not a developer? **[layman.md](layman.md)** explains what Scrapo does — and what it can't do — in plain English.

---

## Why Scrapo

<div align="center">

| **5-tier router** | **Hybrid extractor** | **Model pinning** |
|:---:|:---:|:---:|
| Auto-escalates HTTP → browser → stealth → agent on real failure signals only | Selector-cheap by default; falls back to LLM and self-heals | Strict mode refuses unpinned LLM extraction — extraction can't silently drift |

| **Provenance** | **Deterministic replay** | **Compliance built-in** |
|:---:|:---:|:---:|
| Every chunk carries URL, selector path, byte range, heading trail | Re-extract from archived HTML 6 months later. Diff fields between any two runs | optional robots gate, regex+Luhn PII, geo allow/deny, append-only audit log |

</div>

---

## Architecture

```
                 ┌─────────────────────────────────────────────┐
                 │               scrapo.scrape(...)            │
                 └──────────────────┬──────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
   ① Tier Router              ② Extractor             ③ Document Shaper
   T0 HTTP                    Selector cache           HTML → Markdown
   T1 HTTP+session            ↓ if validation fails    Heading-aware chunks
   T2 Browser                 LLM fallback             Per-chunk provenance
   T3 Browser+stealth         ↓ self-heal              Cross-crawl dedup
   T4 Agent                   selector cache writeback
        │                           │                           │
        └─────────┬─────────────────┴─────────┬─────────────────┘
                  │                           │
             ④ Replay store              ⑤ Policy gate
             SQLite + gzip HTML          robots / PII / geo / audit
                  │                           │
                  └────────────┬──────────────┘
                               │
                       ⑥ Agent surface
                       MCP server + tool schemas
```

```
scrapo/
├── access/      # ① 5-tier router + Bright Data / Oxylabs / Scrapfly / Zyte
├── extract/     # ② hybrid selector + LLM, model pinning
├── shape/       # ③ selectolax markdown + heading chunker
├── replay/      # ④ snapshot store + field-level diff
├── policy/      # ⑤ robots, PII, geo, append-only audit
├── crawl/       # persistent SQLite queue + async scheduler
├── agent/       # ⑥ MCP server + tool schemas
├── api.py       # public scrape / extract / crawl
├── web.py       # local browser UI (scrapo serve)
└── cli.py       # Typer CLI
```

---

## Quickstart

```bash
pip install scrapo

pip install "scrapo[browser,anthropic,mcp]"
playwright install chromium
```

### 1. Scrape one URL

```python
import asyncio, scrapo

async def main():
    res = await scrapo.scrape("https://example.com/")
    print(res["markdown"])
    print("run_id:", res["run_id"])

asyncio.run(main())
```

### 2. Typed extraction (LLM once, selectors forever)

```python
import asyncio, scrapo
from pydantic import BaseModel

class Product(BaseModel):
    name: str
    price: str

async def main():
    res = await scrapo.scrape("https://example.com/widget", schema=Product)
    print(res["extraction"]["data"])      # {'name': 'Widget Pro', 'price': '$42'}
    print(res["extraction"]["method"])    # 'llm'  → first run
                                          # 'selector' → every run after

asyncio.run(main())
```

> First call uses the LLM and **caches the selectors it learns**. Every subsequent call against the same domain + schema uses cached selectors and **zero LLM tokens** — until layout drifts, at which point Scrapo falls back to the LLM, re-derives selectors, and self-heals.

### 3. Recursive crawl

```python
await scrapo.crawl(
    seeds=["https://docs.python.org/3/"],
    max_depth=2,
    same_host_only=True,
)
```

### 4. Replay & diff

```bash
scrapo list                    # recent runs
scrapo replay <run_id>         # re-extract from archived HTML — no network
scrapo diff <run_a> <run_b>    # field-level diff
```

---

## Features

<details>
<summary><b>Cost-aware tier router</b></summary>

| Tier | What it does | When |
|---|---|---|
| **T0** `HTTP` | `httpx` plain GET | static HTML, JSON endpoints |
| **T1** `HTTP_SESSIONED` | + browser-like headers/cookies | soft anti-bot |
| **T2** `BROWSER` | Playwright headless | JS-rendered pages |
| **T3** `BROWSER_STEALTH` | + stealth + residential proxy | hard anti-bot |
| **T4** `AGENT` | LLM-driven multi-step browser | logins, captchas, flows |

Escalation triggers: Cloudflare/Akamai/PerimeterX/DataDome/Distil fingerprints, HTTP `403 / 429 / 503`, empty body, missing required schema fields.

</details>

<details>
<summary><b>Hybrid selector + LLM extractor</b></summary>

```
┌── cache hit + validates ──→ return (method=selector, llm_calls=0)
│
└── miss / fail ─→ LLM with schema ─→ validate ─→ persist new selectors ─→ return (method=llm)
```

The LLM is asked to return both the JSON payload *and* CSS selectors per field. Returned selectors are verified against the live HTML before being cached, so a hallucinated selector never poisons the cache.

</details>

<details>
<summary><b>Model pinning (Zyte-style, but built in)</b></summary>

```python
from scrapo.extract.pinning import PinnedModel

pin = PinnedModel.make(
    provider="anthropic",
    model_id="claude-opus-4-7",
    prompt_template="…your prompt template…",
)

await scrapo.scrape(url, schema=Product, pin=pin, strict_pin=True)
```

`strict_pin=True` makes the extractor refuse to run if the configured LLM does not match the pin. Silent model drift cannot happen in production.

</details>

<details>
<summary><b>Per-chunk provenance</b></summary>

Every chunk Scrapo emits carries:
```python
{
    "url":           "https://example.com/page",
    "selector_path": "markdown://Features/Pricing",
    "byte_start":    8421,
    "byte_end":      9842,
    "heading_trail": ["Features", "Pricing"],
    "chunk_hash":    "ab12cd34…"
}
```

You can trace any LLM citation back to a specific section of a specific URL.

</details>

<details>
<summary><b>Deterministic replay & diff</b></summary>

Every fetch persists raw HTML, headers, screenshots, and the typed extraction:

```bash
scrapo replay 9f3e1c…       # re-extract from archived HTML, no network
scrapo diff 9f3e1c… abc123… # field-level diff, with notes when model/schema changed
```

Sample diff output:
```
diff 9f3e1c…  vs  abc123…
  HTML changed
  ! model changed: anthropic:claude-opus-4-7 → anthropic:claude-sonnet-4-6 (extraction may drift)
  field changes:
    - price: '$42' → '$45'
    - in_stock: True → False
```

</details>

<details>
<summary><b>Built-in compliance layer</b></summary>

- Optional `robots.txt` parser with per-origin caching
- Regex PII classifier (email, phone, SSN, credit card with Luhn, IPv4, IBAN, passport)
- Geo policy with EU-only preset (`GeoPolicy.eu_only()`) plus custom allow/deny lists
- Append-only JSONL audit log of every scrape, block, geo violation, PII detection

</details>

<details>
<summary><b>BYO proxy adapters</b></summary>

```python
from scrapo.access.adapters.brightdata import BrightDataAdapter
import scrapo

adapter = BrightDataAdapter()  # reads BRIGHTDATA_USERNAME / BRIGHTDATA_PASSWORD
await scrapo.scrape("https://hard-target.com/", proxy_adapter=adapter)
```

Built-in: `brightdata`, `oxylabs`, `scrapfly`, `zyte`. Implement the `ProxyAdapter` protocol for anything else:

```python
from scrapo.access.adapters.base import ProxyConfig

class MyAdapter:
    name = "my-vendor"
    async def get_proxy(self, geo=None):
        return ProxyConfig(url="http://user:pass@my-proxy:8080", region=geo)
```

</details>

<details>
<summary><b>BYO LLM adapters</b></summary>

| Provider | Adapter | Install | Default model |
|---|---|---|---|
| Anthropic Claude | `anthropic` (default) | `pip install "scrapo[anthropic]"` | `claude-opus-4-7` |
| OpenAI | `openai` | `pip install "scrapo[openai]"` | `gpt-4o-mini` |
| Google Gemini | `gemini` | `pip install "scrapo[gemini]"` | `gemini-2.5-flash` |
| Mock (offline) | `mock` (tests) | always available | — |

Anthropic adapter uses **prompt caching** on the schema block, so repeated extractions against the same Pydantic schema are cheap.

</details>

---

## CLI

```bash
scrapo scrape https://example.com/
scrapo scrape https://example.com/ --max-tier 3 --screenshot --out-md page.md
scrapo crawl https://docs.python.org/3/ --max-depth 2 --max-pages 100
scrapo list --limit 10
scrapo replay <run_id>
scrapo diff <run_a> <run_b>
scrapo audit                    # tail the append-only audit log
scrapo adapters                 # list registered proxy adapters
scrapo serve                    # local browser UI at http://127.0.0.1:8787
scrapo mcp                      # run the MCP server over stdio
```

---

## Use as an MCP server

Scrapo ships an MCP server exposing five tools to any MCP-compatible client (Claude Code, Claude Desktop, Cursor, …):

```
scrapo_scrape    scrapo_crawl    scrapo_replay    scrapo_diff    scrapo_list_runs
```

```bash
pip install "scrapo[mcp]"
scrapo mcp
```

Add to your client config:

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

---

## Configuration

Every default is overridable via env var:

| Variable | Default | |
|---|---|---|
| `SCRAPO_DATA_DIR` | platform user-data dir | SQLite + snapshots + audit log |
| `SCRAPO_USER_AGENT` | `scrapo/0.1` | UA for HTTP and robots |
| `SCRAPO_TIMEOUT` | `30` | request timeout (s) |
| `SCRAPO_CONCURRENCY` | `8` | crawl concurrency |
| `SCRAPO_RESPECT_ROBOTS` | `0` | `1` to enable robots gate |
| `SCRAPO_PII_FILTER` | `0` | `1` to flag PII in audit log |
| `SCRAPO_PROXY_ADAPTER` | _unset_ | default registered adapter name |
| `SCRAPO_LLM_ADAPTER` | `anthropic` | default LLM provider |
| `SCRAPO_LLM_MODEL` | `claude-opus-4-7` | default model id |
| `SCRAPO_GEO` | _unset_ | default proxy region |
| `ANTHROPIC_API_KEY` | — | for the Claude adapter |
| `OPENAI_API_KEY` | — | for the OpenAI adapter |
| `GEMINI_API_KEY` | — | for the Gemini adapter |

---

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

The suite is **fully offline** — no test hits the network or a paid LLM. It covers signals, shape, extract, replay, policy, dedup, queue, router, adapters, the local web UI, config, and end-to-end scrape with monkeypatched fetchers.

---

## Project status

Alpha. The public API (`scrape`, `extract`, `crawl`) is stable; tier escalation, model pinning, replay schema, and the MCP tool surface are stable. Parts that are intentionally lightweight today and slated for hardening: T4 agent driver, full Stagehand-style action caching, S3 snapshot adapter, hosted control plane.

See [CHANGELOG.md](CHANGELOG.md) for release notes.

---

## License

[MIT](LICENSE) © Scrapo contributors

<div align="center">

⭐ **Star the repo** if Scrapo saved you a week of glue code.

</div>
