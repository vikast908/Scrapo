<div align="center">

# 🕸️ Scrapo

**The web-scraping library agents deserve.**

*Selector-cheap. LLM-resilient. Replay-safe. Self-hosted.*

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776AB?style=flat-square)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-22c55e?style=flat-square)](LICENSE)
[![Status: beta](https://img.shields.io/badge/status-beta-3b82f6?style=flat-square)](https://github.com/vikast908/Scrapo)

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

**Managed access**<br>*api-first, proxies, anti-bot*

</td>
</tr>
</table>

Plus a feature nobody else ships: **deterministic replay** of every fetch, so extraction drift is auditable.

> Not a developer? **[LAYMAN.md](LAYMAN.md)** explains what Scrapo does, and what it cannot do, in plain English.

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
├── access/      # (1) 5-tier router + pooled browser + request interception + agent driver + action cache + proxy adapters & rotating pool + Interact actions (incl. scroll_until / click_until)
├── extract/     # (2) embedded metadata (JSON-LD/OG/microdata) + hybrid selector + LLM (scalar & list fields), model pinning, cost-aware budget
├── shape/       # (3) markdown + heading chunker + content-type dispatch (HTML / JSON / feed / PDF / text)
├── replay/      # (4) SQLite metadata + pluggable snapshot store (local or S3) + field-level diff
├── policy/      # (5) robots, PII (flag or redact), geo, append-only audit
├── crawl/       # persistent SQLite queue + async scheduler + sitemap discovery + rel=next pagination + batch
├── agent/       # (6) MCP server + tool schemas
├── server/      # (7) self-hosted watch control plane: persistent WatchStore + WatchScheduler + webhook/callback notifiers
├── results.py   # typed ScrapeResult / CrawlResult / ExtractionView
├── watch.py     # watch(url) -> Watch.refresh() -> ChangeSet (change tracking)
├── export.py    # to_jsonl / to_csv dataset writers
├── sync.py      # synchronous facade (scrape_sync / crawl_sync / ...)
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

Before T0, **API-first resolution** (see the feature section below) short-circuits the whole ladder for known sites (e.g. Wikipedia → its REST API), so the cheapest path of all is one the tier router never sees.

Escalation triggers: Cloudflare/Akamai/PerimeterX/DataDome/Distil fingerprints, HTTP `403 / 429 / 503`, empty body, missing required schema fields, and unrendered single-page-app shells (lots of script, almost no rendered text). `Budget(max_tier=..., max_llm_calls=..., max_cost_usd=...)` caps how far it goes. The browser tiers block images/fonts/media/css by default and capture JSON XHR/fetch responses onto the result. The Tier-4 driver records the action sequence it used to reach a goal on a host (`agent_actions.sqlite`) and replays it on later runs with zero LLM tokens, self-healing back to the model only when a recorded step no longer applies (`SCRAPO_AGENT_ACTION_CACHE=0` to disable).

</details>

<details>
<summary><b>API-first: known sites resolve to their public API</b></summary>

Some sites CAPTCHA every scraper yet publish the *same* content through a clean, unauthenticated API. When Scrapo recognises such a URL it fetches the API **before** the tier router runs — skipping the whole HTTP → browser → stealth → agent escalation and the bot wall that defeats it. Wikipedia is the headline case: it blocks scrapers aggressively but serves every article through its REST API (`/api/rest_v1/page/html/{title}`), and the same contract covers its Wikimedia sister projects (Wiktionary, Wikinews, Wikibooks, Wikiquote, Wikiversity, Wikivoyage, Wikisource).

```python
r = scrape_sync("https://en.wikipedia.org/wiki/Albert_Einstein")
r.via        # "api:wikipedia"  — served from the REST API, no CAPTCHA, no browser
r.url        # "https://en.wikipedia.org/wiki/Albert_Einstein"  — the page you asked for
r.markdown   # clean article text, through the normal markdown/chunk/extraction pipeline
```

The REST HTML runs through the same markdown / chunk / provenance / extraction pipeline as any page, and conditional-GET + replay still apply. On by default; turn it off per call with `scrape(api_first=False)` (CLI `--no-api-first`, MCP `api_first=false`) or globally with `SCRAPO_API_FIRST=0`. It's also suppressed automatically whenever you force a tier, pass `actions`, or ask for a `screenshot` — i.e. when you explicitly want the live page. Agents get this for free: the `scrapo_scrape` MCP tool documents it and the result's `via` field reports when it fired. The provider registry (`scrapo/access/api_providers.py`) is a plain tuple, so adding a site is a few lines.

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
<summary><b>Zero-LLM extraction from embedded structured data</b></summary>

Before the selector cache or the LLM, Scrapo tries to satisfy your schema straight from data the page already hands out: schema.org JSON-LD (`<script type="application/ld+json">`), OpenGraph / Twitter / vertical `<meta>` tags, and microdata `itemprop` attributes. A huge fraction of commercial pages (products, articles, recipes, jobs, events) embed this, so the common case becomes free, deterministic, and immune to layout drift.

```python
class Product(BaseModel):
    name: str
    price: str | None = None

res = await scrapo.scrape("https://shop.example.com/widget", schema=Product)
print(res.extraction.method)   # 'metadata'  (no selector cache, no LLM)
print(res.cost_usd)            # 0.0
```

The extraction ladder is now: **embedded metadata -> selector cache -> LLM**. It is conservative: it only returns when every required field was sourced and the object validates, otherwise it falls through to the existing path, so it never costs correctness. The bare `<title>` tag is not treated as a source (it is page chrome, not a structured annotation). On by default; `Config(metadata_extraction=False)` or `SCRAPO_METADATA_EXTRACTION=0` disables it.

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
<summary><b>Main-content extraction (cleaner Markdown)</b></summary>

Turn on a readability-style pass that strips site furniture — nav, sidebars, footers, cookie banners, ads — and keeps just the article body before converting to Markdown. Output reads like a clean document, which is what you want for RAG/LLM ingestion.

```python
res = await scrapo.scrape("https://blog.example.com/post", main_content=True)
print(res.markdown)   # boilerplate removed; provenance/chunks still attached
```

Off by default (full-page conversion). Enable per call (`main_content=True`), globally (`Config(main_content=True)`), or via `SCRAPO_MAIN_CONTENT=1`. It scores candidate containers by text vs. link density and prefers `<article>` / `<main>` / `role=main`; if it can't confidently find a main region it falls back to the full page, so it never silently drops content.

</details>

<details>
<summary><b>Map a site (discover URLs without scraping)</b></summary>

```python
urls = await scrapo.map_site(["https://docs.example.com/"], max_depth=2)
```

Merges each origin's `sitemap.xml` with a bounded same-host link crawl and returns a sorted, de-duplicated, SSRF-filtered URL list — a fast "table of contents" before you decide what to actually scrape. `same_host_only` treats `www.` as the same host; binary/asset URLs are skipped. Also `scrapo map` (CLI) and `scrapo_map` (MCP).

</details>

<details>
<summary><b>Batch scrape (a list of URLs, concurrently)</b></summary>

```python
items = await scrapo.batch_scrape(urls, schema=Product, main_content=True)
for it in items:                       # results in input order
    if it.error: ...                   # per-URL error isolation; one failure never aborts the batch
    else: save(it.result)

async for it in scrapo.batch_scrape_stream(urls):   # or stream as they complete
    save(it)
```

Scrapes exactly the URLs you give it (not a recursive crawl), with bounded concurrency and one shared browser pool + stores across the batch. Also `scrapo batch` (CLI) and `scrapo_batch` (MCP).

</details>

<details>
<summary><b>Interact: deterministic browser actions (no LLM)</b></summary>

Script the steps to reach content behind buttons, tabs, or simple forms — no model in the loop, so it's cheap and repeatable:

```python
res = await scrapo.scrape(
    "https://example.com/app",
    actions=[
        {"type": "click", "selector": "button#load-more"},
        {"type": "wait_for_selector", "selector": ".results"},
        {"type": "scroll", "amount": 2000},
        {"type": "type", "selector": "input#q", "text": "widgets"},
    ],
)
```

Supported actions: `goto, click, type, fill, press, scroll, scroll_until, click_until, wait, wait_for_selector, screenshot`. Each `goto` target is checked by the SSRF guard. Passing `actions` routes straight to a browser session and works with **no agent driver configured**. For open-ended, model-driven flows, the Tier-4 `AgentDriver` (with token-free action replay) still applies.

**Auto-pagination.** Two verbs handle the common "there's more content below" cases without you scripting a fixed number of steps:

```python
# Infinite scroll: keep scrolling until the .item count stops growing (bounded by `times`).
actions=[{"type": "scroll_until", "selector": ".item", "times": 10}]

# Click-to-load: keep clicking "Load more" until the button disappears.
actions=[{"type": "click_until", "selector": "button.load-more"}]
```

Both are bounded by a `times` round cap so a genuinely endless feed cannot loop forever; `scroll_until` stops early the first round that adds nothing new.

</details>

<details>
<summary><b>Synchronous API (scripts and notebooks)</b></summary>

Scrapo is async at its core, but you don't have to be. Every buffered entry point has a `*_sync` twin with the identical signature:

```python
import scrapo

res = scrapo.scrape_sync("https://example.com/")
items = scrapo.batch_scrape_sync(["https://a.com/", "https://b.com/"])
```

`scrape_sync`, `extract_sync`, `crawl_sync`, `map_site_sync`, and `batch_scrape_sync` run the coroutine to completion. They even work inside an already-running event loop (e.g. a Jupyter cell) by using a worker thread, so you never see "asyncio.run() cannot be called from a running event loop". The streaming generators (`crawl_stream` / `batch_scrape_stream`) stay async-only by design.

</details>

<details>
<summary><b>Export results to JSONL / CSV</b></summary>

Turn a batch or crawl into a dataset file with no glue code:

```python
from scrapo.export import to_jsonl, to_csv

items = await scrapo.batch_scrape(urls, schema=Product)
to_jsonl(items, "out.jsonl")          # one JSON object per line
to_csv(items, "out.csv")              # flat table; extraction fields become columns
```

An errored batch item becomes a row with its `error` set; nested extraction values are JSON-encoded into the CSV cell. Also on the CLI: `scrapo batch ... --out-jsonl out.jsonl --out-csv out.csv` and `scrapo crawl ... --out-jsonl pages.jsonl`.

</details>

<details>
<summary><b>Watch control plane (self-hosted)</b></summary>

`watch()` is in-process. When you want a *list* of watches that runs on a schedule, survives restarts, and fires alerts, `scrapo.server` is the engine:

```python
from scrapo.server import WatchStore, WatchScheduler, WebhookNotifier

store = WatchStore(cfg.watch_db)
sched = WatchScheduler(store, notifier=WebhookNotifier())
await sched.add(
    "https://example.com/pricing",
    interval_seconds=3600,
    webhook_url="https://hooks.example/scrapo",
    schema=Pricing,
)
await sched.run_forever()              # tick, check due watches, POST on change
```

`WatchStore` persists watch definitions in SQLite; `WatchScheduler` re-checks the due ones (re-scrape plus field diff, kept cheap by conditional GET) and, on a change, calls a `Notifier` (`WebhookNotifier` POSTs a JSON payload; `CallbackNotifier` calls your function). Manage it from the CLI: `scrapo watch-add`, `watch-list`, `watch-remove`, `watch-run`. A full multi-tenant web console with auth is a separate deployable app built on top of this engine, not part of the library.

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

Scrapo is **model-agnostic**: native adapters for Anthropic and Gemini, and one generic adapter for *any* OpenAI-wire-compatible endpoint.

| Provider | `SCRAPO_LLM_ADAPTER` | Install | Notes |
|---|---|---|---|
| Anthropic Claude | `anthropic` | `pip install "scrapo[anthropic]"` | native SDK; prompt-caches the schema block |
| Google Gemini | `gemini` | `pip install "scrapo[gemini]"` | native SDK |
| OpenAI | `openai` | `pip install "scrapo[openai]"` | default model `gpt-4o-mini` |
| DeepSeek | `deepseek` | `pip install "scrapo[openai]"` | default `deepseek-v4-flash` |
| OpenRouter | `openrouter` | `pip install "scrapo[openai]"` | `OPENROUTER_API_KEY`, `SCRAPO_OPENROUTER_MODEL` |
| Ollama (local) | `ollama` | `pip install "scrapo[openai]"` | no key; `OLLAMA_BASE_URL` (default `http://localhost:11434/v1`), `SCRAPO_OLLAMA_MODEL` |
| Any OpenAI-compatible | `openai-compatible` | `pip install "scrapo[openai]"` | set `SCRAPO_LLM_BASE_URL` (+ `SCRAPO_LLM_API_KEY`, `SCRAPO_LLM_MODEL`) — vLLM, LM Studio, Groq, Together, gateways, … |
| Mock (offline) | `mock` | always available | deterministic, no network |

Pick a provider with `SCRAPO_LLM_ADAPTER`; if unset, Scrapo **auto-detects** from whichever API key is present and falls back to the mock when none is — so a missing key never triggers a surprise call to a specific paid provider. The OpenAI-compatible providers all ride one generic adapter (`base_url` + `api_key` + `model`), so any endpoint speaking the OpenAI chat protocol works — just point `SCRAPO_LLM_BASE_URL` at it. JSON-object mode is requested where supported and transparently dropped for endpoints that reject it, so bare local models still work. The Anthropic adapter uses **prompt caching** on the schema block, so repeated extractions against the same schema are cheap.

</details>

---

## CLI

```bash
scrapo scrape https://example.com/
scrapo scrape https://example.com/ --max-tier 3 --screenshot --out-md page.md
scrapo crawl https://docs.python.org/3/ --max-depth 2 --max-pages 100
scrapo map https://docs.python.org/3/ --max-depth 2 --out urls.txt   # discover URLs, no scrape
scrapo batch https://a.com/ https://b.com/ --main-content              # scrape a list concurrently
scrapo scrape https://example.com/ --main-content                     # strip boilerplate
scrapo batch https://a.com/ https://b.com/ --out-jsonl out.jsonl       # scrape a list, export JSONL
scrapo list --limit 10
scrapo replay <run_id>
scrapo diff <run_a> <run_b>
scrapo audit                    # tail the append-only audit log
scrapo adapters                 # list registered proxy adapters
scrapo watch-add https://example.com/pricing --interval 3600 --webhook https://hooks.example/x
scrapo watch-list               # list persisted watches
scrapo watch-run                # run the watch scheduler loop (POSTs on change)
scrapo serve                    # local browser UI at http://127.0.0.1:8787
scrapo mcp                      # run the MCP server over stdio
```

---

## Use as an MCP server

Scrapo ships an MCP server exposing seven tools to any MCP-compatible client (Claude Code, Claude Desktop, Cursor, and others):

```
scrapo_scrape    scrapo_crawl    scrapo_map    scrapo_batch
scrapo_replay    scrapo_diff     scrapo_list_runs
```

For agents this is the path of least resistance: hand `scrapo_scrape` a URL and it returns clean markdown + provenance-tagged chunks, re-scrapes come back fast via conditional GET, and **known sites with a public API (Wikipedia and its Wikimedia sister projects) are auto-routed through that API** — so a Wikipedia URL just works with no CAPTCHA and no `max_tier` tuning, and the result reports `via="api:wikipedia"`.

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
| `SCRAPO_MAIN_CONTENT` | `0` | `1` to strip boilerplate (nav/sidebar/footer/ads) before markdown |
| `SCRAPO_METADATA_EXTRACTION` | `1` | `0` to disable the zero-LLM JSON-LD/OpenGraph/microdata extraction rung |
| `SCRAPO_API_FIRST` | `1` | `0` to disable API-first resolution (e.g. Wikipedia → its REST API) |
| `SCRAPO_WATCH_POLL` | `30` | how often (s) `scrapo watch-run` wakes to check for due watches |
| `SCRAPO_ALLOW_PRIVATE_HOSTS` | `0` | `1` to allow fetching private/loopback addresses |
| `SCRAPO_SNAPSHOT_BACKEND` | `local` | `local` or `s3://bucket/prefix` |
| `SCRAPO_BROWSER_BLOCK_RESOURCES` | `1` | `0` to let the browser tier load images/fonts/media/css |
| `SCRAPO_BROWSER_CAPTURE_XHR` | `1` | `0` to skip capturing JSON XHR/fetch responses |
| `SCRAPO_AGENT_DRIVER` | unset | `llm` to enable the built-in Tier-4 agent driver |
| `SCRAPO_AGENT_ACTION_CACHE` | `1` | `0` to disable recording/replaying Tier-4 agent action sequences |
| `SCRAPO_PROXY_ADAPTER` | unset | default registered adapter name |
| `SCRAPO_PROXY_URLS` | unset | comma-separated proxy URLs for the rotating pool (used when no adapter is set) |
| `SCRAPO_PROXY_COOLDOWN` | `120` | seconds a parked proxy stays out of rotation |
| `SCRAPO_LLM_ADAPTER` | auto | LLM provider: `anthropic`, `gemini`, `openai`, `deepseek`, `openrouter`, `ollama`, `openai-compatible`. Unset = auto-detect from available key, else mock |
| `SCRAPO_LLM_MODEL` | provider default | model id for the generic / `openai-compatible` adapter |
| `SCRAPO_LLM_BASE_URL` | unset | base URL for the `openai-compatible` provider (vLLM, LM Studio, gateways, …); its presence also triggers auto-detect |
| `SCRAPO_LLM_API_KEY` | unset | API key for the `openai-compatible` provider |
| `SCRAPO_OPENAI_MODEL` | `gpt-4o-mini` | model id for the OpenAI adapter |
| `SCRAPO_DEEPSEEK_MODEL` | `deepseek-v4-flash` | model id for the DeepSeek adapter (e.g. `deepseek-v4-flash`, `deepseek-v4-pro`) |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | DeepSeek API base (override for a proxy/gateway) |
| `SCRAPO_OPENROUTER_MODEL` | `openai/gpt-4o-mini` | model id for the OpenRouter adapter |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter API base |
| `SCRAPO_OLLAMA_MODEL` | `llama3.1` | model id for the Ollama adapter |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama API base |
| `SCRAPO_GEO` | unset | default proxy region |
| `SCRAPO_LOG_LEVEL` | `INFO` | log level for the CLI / MCP server |
| `SCRAPO_LOG_FORMAT` | `console` | `console` or `json` |
| `ANTHROPIC_API_KEY` | unset | for the Claude adapter |
| `OPENAI_API_KEY` | unset | for the OpenAI adapter |
| `GEMINI_API_KEY` | unset | for the Gemini adapter |
| `DEEPSEEK_API_KEY` | unset | for the DeepSeek adapter |
| `OPENROUTER_API_KEY` | unset | for the OpenRouter adapter |

---

## Tests

```bash
pip install -e ".[dev]"
pytest -q                     # 367 fully-offline tests
ruff check .
mypy scrapo/

# real-browser end-to-end tests (separate; needs Chromium)
pip install -e ".[dev,browser]"
playwright install chromium
pytest -m integration
```

The default suite is **fully offline**; no test hits the network or a paid LLM. It covers embedded-metadata extraction (JSON-LD / OpenGraph / microdata), the synchronous facade, the JSONL/CSV exporters, the auto-pagination actions (`scroll_until` / `click_until`), the watch control plane (store CRUD, scheduler ticks, notifier dispatch), signals (including SPA-shell detection), SSRF, the HTTP retry path, conditional GET / 304-archive reuse, `watch()` change tracking and `crawl_stream`, shape, extract (cache eviction, budget, cost), replay (and the schema migration), policy, dedup, queue, router, proxy adapters and the rotating pool (rotation, cooldown, hard vs. soft failures, upstream fallback, the tier feedback loop), the agent driver and its action cache (record / replay / self-heal / eviction with fake page + scripted LLM), the local web UI, config, and end-to-end scrape with monkeypatched fetchers.

A separate `pytest -m integration` suite drives a real headless Chromium against a local fixture server to validate the browser tier, the auto-pagination actions, and the end-to-end metadata path (deselected by default; a dedicated CI job runs it).

📊 **Full test & benchmark results** — coverage, performance/throughput, extraction quality vs. trafilatura/readability/newspaper4k/crawl4ai, anti-bot & TLS fingerprinting, WARC-replay and SPA scraping, and sustained-load stability — are documented in **[TESTING.md](TESTING.md)**.

---

## Contributing

Issues and PRs welcome. See [CONTRIBUTORS.md](CONTRIBUTORS.md) for the dev setup, quality gates, and house rules, the [Tests](#tests) section above, and [SECURITY.md](SECURITY.md) for reporting vulnerabilities.

---

## Project status

Beta. The public API (`scrape`, `extract`, `crawl`, `crawl_stream`, `watch`, `map_site`, `batch_scrape`, plus the `*_sync` twins) is stable, as are tier escalation, model pinning, replay schema, typed results, embedded-metadata + list extraction, content-type routing, main-content extraction, the pluggable snapshot store, the rotating proxy pool, conditional requests, the JSONL/CSV exporters, and the MCP tool surface. The reference Tier-4 agent driver (with action caching: record the steps to a goal, replay them token-free, self-heal back to the LLM when a step breaks) and the in-browser request interception now have a real-browser integration suite (`pytest -m integration`) on top of the offline tests. The watch control plane (`scrapo.server`) ships the self-hosted scheduler engine: persist a list of watches, run them on a schedule, and POST a webhook on change. A full multi-tenant web console with auth is still a separate deployable app built on top of that engine, not part of the library.

See [CHANGELOG.md](CHANGELOG.md) for release notes.

---

## License

[MIT](LICENSE) (c) Scrapo contributors

<div align="center">

⭐ **Star the repo** if Scrapo saved you a week of glue code.

</div>
