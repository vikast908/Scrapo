<div align="center">

# 🕸️ Scrapo

**The web-scraping library agents deserve.**

*Selector-cheap. LLM-resilient. Replay-safe. Self-hosted.*

[![CI](https://github.com/vikast908/Scrapo/actions/workflows/ci.yml/badge.svg)](https://github.com/vikast908/Scrapo/actions/workflows/ci.yml)
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

[Quickstart](#quickstart) | [Architecture](#architecture) | [Features](#features) | [Why Scrapo](#why-scrapo) | [CLI](#cli) | [MCP](#use-as-an-mcp-server)

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

Plus a feature nobody else ships: **deterministic replay** of every fetch, so extraction drift is auditable.

> Not a developer? **[layman.md](layman.md)** explains what Scrapo does, and what it cannot do, in plain English.

---

## Why Scrapo

<div align="center">

| **5-tier router** | **Hybrid extractor** | **Model pinning** |
|:---:|:---:|:---:|
| Auto-escalates HTTP, browser, stealth, agent on real failure signals only | Selector-cheap by default; falls back to LLM and self-heals | Strict mode refuses unpinned LLM extraction, so extraction cannot silently drift |

| **Provenance** | **Deterministic replay** | **Safe by default** |
|:---:|:---:|:---:|
| Every chunk carries URL, selector path, byte range, heading trail | Re-extract from archived HTML 6 months later; diff fields between any two runs | SSRF guard, opt-in robots gate, regex+Luhn PII, geo allow/deny, append-only audit log |

</div>

---

## Architecture

```
                 +---------------------------------------------+
                 |               scrapo.scrape(...)            |
                 +-------------------+-------------------------+
                                     |
        +----------------------------+----------------------------+
        |                            |                            |
   (1) Tier Router            (2) Extractor             (3) Document Shaper
   T0 HTTP (retries)          Selector cache (host key)  HTML to Markdown
   T1 HTTP+session            -> if validation fails     Heading-aware chunks
   T2 Browser                 LLM fallback (budgeted)    Per-chunk provenance
   T3 Browser+stealth         -> self-heal / evict       Cross-crawl dedup
   T4 Agent                   selector cache writeback
        |                            |                            |
        +---------+------------------+---------+-------------------+
                  |                            |
            (4) Replay store             (5) Policy gate
            SQLite (WAL) + gzip HTML      SSRF / robots / PII / geo / audit
                  |                            |
                  +-------------+--------------+
                                |
                        (6) Agent surface
                        MCP server + tool schemas
```

```
scrapo/
├── access/      # (1) 5-tier router + pooled browser + request interception + agent driver + action cache + proxy adapters & rotating pool
├── extract/     # (2) hybrid selector + LLM (scalar & list fields), model pinning, cost-aware budget
├── shape/       # (3) markdown + heading chunker + content-type dispatch (HTML / JSON / feed / PDF / text)
├── replay/      # (4) SQLite metadata + pluggable snapshot store (local or S3) + field-level diff
├── policy/      # (5) robots, PII (flag or redact), geo, append-only audit
├── crawl/       # persistent SQLite queue + async scheduler + sitemap discovery + rel=next pagination
├── agent/       # (6) MCP server + tool schemas
├── results.py   # typed ScrapeResult / CrawlResult / ExtractionView
├── watch.py     # watch(url) -> Watch.refresh() -> ChangeSet (change tracking)
├── security.py  # SSRF guard for fetch targets
├── _db.py       # tuned SQLite connections (WAL, busy timeout)
├── logging.py   # structlog setup for the CLI / MCP server
├── api.py       # public scrape / extract / crawl / crawl_stream
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
    res = await scrapo.scrape("https://example.com/")   # res is a typed ScrapeResult
    print(res.markdown)
    print("run_id:", res.run_id)
    # res["markdown"] / res.get("status") still work too (back-compat with the 0.1 dict)

asyncio.run(main())
```

### 2. Typed extraction, including lists (LLM once, selectors forever)

```python
import asyncio, scrapo
from pydantic import BaseModel

class Offer(BaseModel):
    name: str
    price: str

class Listing(BaseModel):
    page_title: str
    offers: list[Offer] = []        # array fields become repeated-element extraction

async def main():
    res = await scrapo.scrape("https://example.com/shop", schema=Listing)
    print(res.extraction.data)      # {'page_title': '...', 'offers': [{'name': ..., 'price': ...}, ...]}
    print(res.extraction.method)    # 'llm' on the first run, 'selector' after
    print(res.cost_usd)             # 0.0 once selectors are cached

asyncio.run(main())
```

> First call uses the LLM and **caches the selectors it learns** (keyed by host + schema; for `list[Model]` fields it caches a container selector plus per-subfield selectors). Every subsequent call against that host + schema uses cached selectors and **zero LLM tokens**. When the layout drifts, validation fails, Scrapo falls back to the LLM, re-derives selectors, and self-heals; a cache entry that keeps failing is evicted automatically.

### 3. Recursive crawl

```python
await scrapo.crawl(
    seeds=["https://docs.python.org/3/"],
    max_depth=2,
    same_host_only=True,
)
```

### 4. Replay and diff

```bash
scrapo list                    # recent runs
scrapo replay <run_id>         # re-extract from archived HTML, no network
scrapo diff <run_a> <run_b>    # field-level diff
```

---

## Features

<details>
<summary><b>Cost-aware tier router</b></summary>

| Tier | What it does | When |
|---|---|---|
| **T0** `HTTP` | `httpx` plain GET, with bounded retry/backoff on 429/5xx and transport errors | static HTML, JSON endpoints |
| **T1** `HTTP_SESSIONED` | + browser-like headers/cookies | soft anti-bot |
| **T2** `BROWSER` | Playwright headless | JS-rendered pages, SPA shells |
| **T3** `BROWSER_STEALTH` | + stealth + residential proxy | hard anti-bot |
| **T4** `AGENT` | LLM-driven multi-step browser via a pluggable `AgentDriver` (a reference `LLMAgentDriver` ships in; `SCRAPO_AGENT_DRIVER=llm`), with **action caching** so a repeated goal replays without the LLM | logins, captchas, flows |

Escalation triggers: Cloudflare/Akamai/PerimeterX/DataDome/Distil fingerprints, HTTP `403 / 429 / 503`, empty body, missing required schema fields, and unrendered single-page-app shells (lots of script, almost no rendered text). `Budget(max_tier=..., max_llm_calls=..., max_cost_usd=...)` caps how far it goes. The browser tiers block images/fonts/media/css by default and capture JSON XHR/fetch responses onto the result. The Tier-4 driver records the action sequence it used to reach a goal on a host (`agent_actions.sqlite`) and replays it on later runs with zero LLM tokens, self-healing back to the model only when a recorded step no longer applies (`SCRAPO_AGENT_ACTION_CACHE=0` to disable).

</details>

<details>
<summary><b>Content-type aware: HTML, JSON, feeds, PDFs</b></summary>

A URL is not always an HTML page, so `scrape()` dispatches on `Content-Type` (with a little body sniffing):

| Content | What you get | `result.kind` |
|---|---|---|
| `text/html` | the normal selectolax + markdown + chunk pipeline | `html` |
| `application/json` / `ld+json` | pretty-printed JSON as markdown; parsed object on `result.data` | `json` |
| RSS / Atom | a markdown list of entries; parsed items on `result.data` | `feed` |
| `application/pdf` | extracted text (requires `pip install "scrapo[pdf]"`) | `pdf` |
| `text/plain` | the body verbatim | `text` |

</details>

<details>
<summary><b>Hybrid selector + LLM extractor (scalar and list fields)</b></summary>

```
cache hit + validates    -> return (method=selector, llm_calls=0, cost_usd=0)
miss / fail / over budget -> LLM with schema -> validate -> verify + persist selectors -> return (method=llm)
repeated cache failures   -> evict the stale entry, re-derive next run
```

The LLM is asked to return both the JSON payload *and* CSS selectors per field. A scalar field gets a string selector; a `list[Model]` field gets `{"__list__": "<repeating element>", "<subfield>": "<selector relative to it>", ...}`, which Scrapo applies as `tree.css(container)` then per-subfield extraction inside each match. Returned selectors are verified against the live HTML before being cached, so a hallucinated selector never poisons the cache. The cache is keyed by host (not registered domain), so `blog.example.com` and `shop.example.com` never collide.

</details>

<details>
<summary><b>Model pinning (Zyte-style, but built in)</b></summary>

```python
from scrapo.extract.pinning import PinnedModel

pin = PinnedModel.make(
    provider="anthropic",
    model_id="claude-opus-4-7",
    prompt_template="(your prompt template)",
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
    "chunk_hash":    "ab12cd34...",
}
```

You can trace any LLM citation back to a specific section of a specific URL.

</details>

<details>
<summary><b>Deterministic replay and diff</b></summary>

Every fetch persists raw HTML, headers, screenshots, and the typed extraction:

```bash
scrapo replay 9f3e1c...        # re-extract from archived HTML, no network
scrapo diff 9f3e1c... abc123...  # field-level diff, with notes when model/schema changed
```

Sample diff output:
```
diff 9f3e1c...  vs  abc123...
  HTML changed
  ! model changed: anthropic:claude-opus-4-7 -> anthropic:claude-sonnet-4-6 (extraction may drift)
  field changes:
    - price: '$42' -> '$45'
    - in_stock: True -> False
```

`scrapo scrape <url> --diff-last` prints that diff against the previous run of the same URL in one step.

</details>

<details>
<summary><b>Watch a URL for changes (cheap re-scrapes)</b></summary>

Re-scraping a URL the HTTP tier fetched before sends a conditional GET (`If-None-Match` / `If-Modified-Since`). A `304 Not Modified` is rebuilt from the archived snapshot: **no body transfer, no LLM call** (the selector cache makes re-extraction free), and no duplicate snapshot is written. `scrape()` / `crawl()` get this automatically; `Config(conditional_requests=False)` (or `SCRAPO_CONDITIONAL_REQUESTS=0`) turns it off.

`watch()` builds the change-tracking loop on top of that:

```python
import scrapo

w = await scrapo.watch("https://example.com/pricing", schema=Pricing)
# ... later, or on a schedule of your choosing ...
change = await w.refresh()
if change.not_modified:
    print("unchanged (304)")
elif change.changed:
    print(change.summary())          # field-level diff vs. the previous run
    for d in change.field_changes:
        print(d)                     # e.g. price: '$42' -> '$45'
```

`Watch` is in-process: the run history (and the diff) live in the replay store. Persisting a *list* of watches with a built-in scheduler is a hosted-service concern and is intentionally left out.

</details>

<details>
<summary><b>Streaming crawl</b></summary>

```python
async for page in scrapo.crawl_stream(["https://blog.example.com/"], schema=Post):
    save(page)                       # process pages as they complete, not all at the end
```

Breaking out of the loop early stops the crawl and tears the shared browser down. `crawl()` remains the buffered convenience (returns aggregate stats + an `on_page` callback).

</details>

<details>
<summary><b>Resilience and safety</b></summary>

- **SSRF guard.** Every fetch target is checked before a request goes out; loopback, link-local (including `169.254.169.254`), private RFC 1918 / ULA ranges, and well-known local hostnames are refused. IP literals are parsed with `inet_aton`-style semantics, so obfuscated encodings (decimal `2130706433`, hex `0x7f000001`, short-form `127.1`, dotted-octal `0177.0.0.1`) of internal addresses are caught too. Tier-4 agent `goto` actions chosen by the LLM go through the same guard. Set `allow_private_hosts=True` (or `SCRAPO_ALLOW_PRIVATE_HOSTS=1`) for internal scraping. Crawl link discovery applies the same filter and skips obvious binary URLs.
- **Bounded HTTP retries.** Transient `429 / 5xx` and transport errors are retried with exponential backoff and jitter before the router escalates to a heavier tier (`SCRAPO_HTTP_RETRIES`, default `2`).
- **Rotating proxy pool with health checks.** Hand Scrapo a list of proxy URLs (`Config(proxy_urls=[...])` or `SCRAPO_PROXY_URLS="http://a,http://b"`) and the router round-robins across them. Every fetch's outcome is fed back: an HTTP 4xx auth/rate-limit code or an anti-bot fingerprint parks that proxy for `proxy_cooldown_seconds` (it's an IP-level block); a transient 5xx / network error counts toward `max_failures`; a clean fetch resets the streak. `ProxyPool` implements the `ProxyAdapter` protocol, so it composes with the vendor adapters; pass `upstream=<adapter>` to fall back to a managed gateway when every static endpoint is parked. Credentials are stripped from proxy URLs before they're logged.
- **Concurrency-safe storage.** All SQLite stores (replay, selector cache, crawl queue, agent action cache) open in WAL mode with a busy timeout, and the per-store init step is guarded by an `asyncio.Lock` so concurrent first callers can't race on schema setup. Crawl workers honoring per-host `crawl-delay` no longer block workers for *other* hosts.
- **Pluggable snapshot storage.** Replay metadata stays in SQLite, but the page bodies go through a `SnapshotStore`: local files by default (atomic write-then-rename, so a crash mid-write can't leave a partial snapshot recorded as complete), or S3 with `snapshot_backend="s3://bucket/prefix"` (`pip install "scrapo[s3]"`). A corrupt archive is detected on read and falls back to a fresh fetch instead of raising.
- **Browser reuse and lighter pages.** A `TierRouter` launches one headless Chromium lazily and reuses it across fetches (proxy applied per context); the browser tiers also block images/fonts/media/css and surface JSON XHR responses. `TierRouter.aclose()` tears it down; `scrape()` and `crawl()` handle that for you.
- **Cost accounting.** LLM cost is computed per call, recorded on the run, and enforceable via `Budget(max_llm_calls=..., max_cost_usd=...)`.
- **PII handling.** Flag PII in the audit log (`SCRAPO_PII_FILTER=1`), or redact it from the stored snapshot, markdown, and chunks (`SCRAPO_REDACT_SNAPSHOTS=1`).
- **Local UI hardening.** `scrapo serve` binds `127.0.0.1` by default, validates the `Host` header against an allowlist (anti DNS-rebinding), serializes scrapes, and warns loudly if you bind a public interface.

</details>

<details>
<summary><b>Built-in compliance layer</b></summary>

- Optional `robots.txt` parser with per-origin caching (off by default; set `SCRAPO_RESPECT_ROBOTS=1`)
- Regex PII classifier (email, phone, SSN, credit card with Luhn, IPv4, IBAN, passport), flag or redact
- Geo policy with EU-only preset (`GeoPolicy.eu_only()`) plus custom allow/deny lists
- Append-only JSONL audit log of every scrape, block, geo violation, PII detection

You are responsible for complying with each site's terms of use and applicable law; these are tools, not a guarantee.

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

Got your own list of proxies instead of a managed gateway? Use the built-in rotating pool: it round-robins, tracks per-endpoint health, and parks one that starts getting blocked:

```python
from scrapo.access import ProxyPool

pool = ProxyPool(["http://u:p@a:8080", "http://u:p@b:8080"])     # or ProxyPool.from_env() / Config(proxy_urls=[...])
await scrapo.scrape("https://hard-target.com/", proxy_adapter=pool)
pool.stats()  # per-endpoint successes / failures / cooldown
```

Pass `upstream=BrightDataAdapter()` to `ProxyPool` to fall back to a managed gateway when every endpoint in the pool is cooling down.

</details>

<details>
<summary><b>BYO LLM adapters</b></summary>

| Provider | Adapter | Install | Default model |
|---|---|---|---|
| Anthropic Claude | `anthropic` (default) | `pip install "scrapo[anthropic]"` | `claude-opus-4-7` |
| OpenAI | `openai` | `pip install "scrapo[openai]"` | `gpt-4o-mini` |
| Google Gemini | `gemini` | `pip install "scrapo[gemini]"` | `gemini-2.5-flash` |
| Mock (offline) | `mock` (tests) | always available | n/a |

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

Scrapo ships an MCP server exposing five tools to any MCP-compatible client (Claude Code, Claude Desktop, Cursor, and others):

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

> The SSRF guard is on by default, which matters here: an MCP client driven by an LLM that just read a page cannot be talked into fetching your internal services.

---

## Configuration

Every default is overridable via env var:

| Variable | Default | Notes |
|---|---|---|
| `SCRAPO_DATA_DIR` | platform user-data dir | SQLite + snapshots + audit log |
| `SCRAPO_USER_AGENT` | `scrapo/0.1` | UA for HTTP and robots |
| `SCRAPO_TIMEOUT` | `30` | request timeout (s) |
| `SCRAPO_CONCURRENCY` | `8` | crawl concurrency |
| `SCRAPO_HTTP_RETRIES` | `2` | retries on 429/5xx/transport errors |
| `SCRAPO_CONDITIONAL_REQUESTS` | `1` | `0` to disable conditional GET / 304-archive reuse on re-scrapes |
| `SCRAPO_RESPECT_ROBOTS` | `0` | `1` to enable the robots gate |
| `SCRAPO_PII_FILTER` | `0` | `1` to flag PII in the audit log |
| `SCRAPO_REDACT_SNAPSHOTS` | `0` | `1` to redact PII from stored snapshots/markdown/chunks |
| `SCRAPO_ALLOW_PRIVATE_HOSTS` | `0` | `1` to allow fetching private/loopback addresses |
| `SCRAPO_SNAPSHOT_BACKEND` | `local` | `local` or `s3://bucket/prefix` |
| `SCRAPO_BROWSER_BLOCK_RESOURCES` | `1` | `0` to let the browser tier load images/fonts/media/css |
| `SCRAPO_BROWSER_CAPTURE_XHR` | `1` | `0` to skip capturing JSON XHR/fetch responses |
| `SCRAPO_AGENT_DRIVER` | unset | `llm` to enable the built-in Tier-4 agent driver |
| `SCRAPO_AGENT_ACTION_CACHE` | `1` | `0` to disable recording/replaying Tier-4 agent action sequences |
| `SCRAPO_PROXY_ADAPTER` | unset | default registered adapter name |
| `SCRAPO_PROXY_URLS` | unset | comma-separated proxy URLs for the rotating pool (used when no adapter is set) |
| `SCRAPO_PROXY_COOLDOWN` | `120` | seconds a parked proxy stays out of rotation |
| `SCRAPO_LLM_ADAPTER` | `anthropic` | default LLM provider |
| `SCRAPO_LLM_MODEL` | `claude-opus-4-7` | default model id |
| `SCRAPO_GEO` | unset | default proxy region |
| `SCRAPO_LOG_LEVEL` | `INFO` | log level for the CLI / MCP server |
| `SCRAPO_LOG_FORMAT` | `console` | `console` or `json` |
| `ANTHROPIC_API_KEY` | unset | for the Claude adapter |
| `OPENAI_API_KEY` | unset | for the OpenAI adapter |
| `GEMINI_API_KEY` | unset | for the Gemini adapter |

---

## Tests

```bash
pip install -e ".[dev]"
pytest -q
ruff check .
mypy scrapo/
```

The suite is **fully offline**; no test hits the network or a paid LLM. It covers signals (including SPA-shell detection), SSRF, the HTTP retry path, conditional GET / 304-archive reuse, `watch()` change tracking and `crawl_stream`, shape, extract (cache eviction, budget, cost), replay (and the schema migration), policy, dedup, queue, router, proxy adapters and the rotating pool (rotation, cooldown, hard vs. soft failures, upstream fallback, the tier feedback loop), the agent driver and its action cache (record / replay / self-heal / eviction with fake page + scripted LLM), the local web UI, config, and end-to-end scrape with monkeypatched fetchers.

---

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for the dev setup and house rules, and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

---

## Project status

Alpha. The public API (`scrape`, `extract`, `crawl`, `crawl_stream`, `watch`) is stable, as are tier escalation, model pinning, replay schema, typed results, list extraction, content-type routing, the pluggable snapshot store, the rotating proxy pool, conditional requests, and the MCP tool surface. The reference Tier-4 agent driver (with action caching: record the steps to a goal, replay them token-free, self-heal back to the LLM when a step breaks) and the in-browser request interception are functional but lightly exercised (a real browser is needed to validate them end to end). The library roadmap is otherwise complete; the only thing intentionally left out is a hosted control plane (a scheduler that runs and persists a list of watches, sends alerts, and gives you a web console). That would be a separate deployable service rather than part of the library.

See [CHANGELOG.md](CHANGELOG.md) for release notes.

---

## License

[MIT](LICENSE) (c) Scrapo contributors

<div align="center">

⭐ **Star the repo** if Scrapo saved you a week of glue code.

</div>
