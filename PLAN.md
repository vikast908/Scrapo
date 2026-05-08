# Scrapo — Plan

**Mission:** A Python, self-hosted, OSS scraping library purpose-built for AI / agent pipelines. Composes the strengths of every tool surveyed in `deep-research-report (1).md` while closing the six gaps the market currently leaves to the user.

**Decisions locked in:**
- Language: **Python 3.11+ / asyncio**
- Deployment: **OSS library first** (pip-installable, self-hosted), hosted SaaS deferred
- Vendors: **BYO via adapters** — pluggable proxies, browsers, and LLMs

---

## 1. The Six Gaps Scrapo Closes

From the report, no current tool delivers all of these together:

| # | Capability | Closest competitor | Scrapo's edge |
|---|---|---|---|
| 1 | Multi-tier access routing (HTTP → browser → agent) | Zyte API | Cost-aware auto-escalation with explicit failure signals |
| 2 | Typed extraction with **schema versioning + model pinning** | Zyte | Pinning + selector cache + LLM fallback all in one |
| 3 | Token-aware document shaping with **per-chunk provenance** | Crawl4AI / Jina Reader | Provenance is mandatory, not optional |
| 4 | **Deterministic replay** of any past run | (none) | Snapshot store + diff tool built in |
| 5 | Policy/compliance (PII, robots, geo, audit) | (none integrated) | First-class layer, not an afterthought |
| 6 | Cost-aware routing across access + LLM tiers | (none) | Budget-aware planner per crawl |

---

## 2. Architecture — Six Pillars

```
                 ┌─────────────────────────────────────────────┐
                 │              Scrapo Runtime API             │
                 │  scrapo.scrape(url, schema=..., budget=...) │
                 └──────────────────┬──────────────────────────┘
                                    │
        ┌───────────────────────────┼───────────────────────────┐
        │                           │                           │
   [1] Tier Router            [2] Extractor           [3] Document Shaper
   HTTP → Browser → Agent     Selector → LLM          Markdown + Chunks
        │                           │                           │
        └─────────┬─────────────────┴─────────┬─────────────────┘
                  │                           │
             [4] Replay Store          [5] Policy Gate
             HTML+Output snapshots     PII / robots / geo
                  │                           │
                  └────────────┬──────────────┘
                               │
                       [6] Agent Surface
                       MCP server + tool schemas
```

### Pillar 1 — Cost-aware Tier Router (`scrapo/access/`)

Five tiers, cheapest first, escalate only on failure signals.

| Tier | Tool | Use when | Cost signal |
|---|---|---|---|
| T0 | `httpx` plain GET | Static HTML, RSS, JSON endpoints | Cheapest |
| T1 | `httpx` + headers/cookies/UA rotation | Soft anti-bot, sessioned APIs | Cheap |
| T2 | Playwright headless | JS-rendered pages | Medium |
| T3 | Playwright + stealth + residential proxy adapter | Hard anti-bot | Expensive |
| T4 | Agent loop (Browser Use / Stagehand-style) | Multi-step flows, login walls, captchas | Most expensive |

**Failure signals** that trigger escalation: block-page fingerprints (Cloudflare, Akamai, PerimeterX), HTTP 403/429, empty `<body>`, missing required schema fields after extraction.

**Adapter interface** so users can plug in Bright Data, Oxylabs, Scrapfly, Zyte without code changes:
```python
class ProxyAdapter(Protocol):
    async def get_proxy(self, geo: str | None) -> ProxyConfig: ...
```

### Pillar 2 — Hybrid Extractor (`scrapo/extract/`)

Closes the **determinism vs resilience** gap from §"Comparative matrix" of the report.

1. **Schema-first** — user defines a Pydantic model. That schema is the contract.
2. **Selector cache** — on first run, Scrapo derives CSS/XPath selectors per field (LLM-assisted). Selectors are stored keyed by `(domain, schema_hash)`.
3. **Cheap path** — subsequent runs use cached selectors directly. Fast, deterministic, free.
4. **LLM fallback** — if any required field is missing or fails validation, fall back to LLM extraction (Claude with prompt caching).
5. **Self-healing** — when LLM fallback fires, derive new selectors from the LLM result and update the cache.
6. **Model pinning** — record `(model_id, prompt_hash, schema_version)` per run. Production runs MUST use pinned models; upgrades require explicit migration. (Borrowed from Zyte API.)

```python
@dataclass
class ExtractionResult[T: BaseModel]:
    data: T
    method: Literal["selector", "llm", "hybrid"]
    selectors_used: dict[str, str]
    model_pinned: str | None
    provenance: list[ProvenanceTag]
```

### Pillar 3 — Token-aware Document Shaper (`scrapo/shape/`)

Beats Jina Reader / Firecrawl on **provenance** and **chunking discipline**.

- HTML → Markdown via a custom converter that preserves: headings (with anchor IDs), tables, code blocks, lists, image alt text.
- Section-aware chunking driven by heading hierarchy (not naive char-split).
- Each chunk carries: `url`, `selector_path`, `byte_range`, `heading_trail`, `chunk_hash`.
- Dedup by `chunk_hash` across the whole crawl (saves tokens on repeated nav/footers).
- Optional LLM re-shaping (caption images, summarize tables) as opt-in step.

### Pillar 4 — Deterministic Replay (`scrapo/replay/`)

The single biggest missing feature in the market.

- Every fetch persists: raw HTML, response headers, screenshot (if browser tier), final markdown, extracted JSON, model+prompt hash.
- Storage: SQLite by default, S3-compatible adapter for production.
- `scrapo replay <run_id>` re-runs extraction against archived HTML — answers "why did the output change?" without re-hitting the site.
- `scrapo diff <run_id_a> <run_id_b>` shows field-level deltas.

### Pillar 5 — Policy & Compliance Gate (`scrapo/policy/`)

Addresses the legal posture concerns flagged in the report's exec summary (CFAA, GDPR, UK).

- `robots.txt` + sitemap respect (configurable strictness).
- PII classifier on extracted output (regex + optional model). Auto-redact or flag.
- Geo-aware routing — can be locked to specific residency (EU-only proxies, etc.).
- Audit log: every fetch records URL, timestamp, tier used, proxy region, policy decisions. Append-only.
- `terms_of_service` adapter pattern — domain-specific allow/deny rules.

### Pillar 6 — Agent-Native Surface (`scrapo/agent/`)

Match the MCP / tool-use trend already established by Playwright, Apify, Scrapfly.

- **MCP server** exposing: `scrape`, `crawl`, `extract`, `replay`, `diff` as MCP tools.
- Tool schemas auto-generated from the Pydantic API — works with Claude tool-use, OpenAI functions, Gemini.
- Streaming JSON output for long crawls.
- Session persistence (CDP profile reuse) for authenticated workflows.

---

## 3. Module Layout

```
scrapo/
├── __init__.py              # Public API: scrape, crawl, extract
├── access/
│   ├── router.py            # Tier router + escalation logic
│   ├── http_tier.py         # T0/T1
│   ├── browser_tier.py      # T2/T3 (Playwright)
│   ├── agent_tier.py        # T4 (browser-use style loop)
│   └── adapters/
│       ├── brightdata.py
│       ├── oxylabs.py
│       ├── scrapfly.py
│       └── zyte.py
├── extract/
│   ├── schema.py            # Pydantic schema registry + versioning
│   ├── selector_cache.py    # SQLite-backed selector store
│   ├── llm_extractor.py     # Claude/OpenAI/Gemini adapters
│   └── pinning.py           # Model pinning enforcement
├── shape/
│   ├── markdown.py
│   ├── chunker.py           # Heading-aware chunking
│   └── provenance.py
├── replay/
│   ├── store.py             # SQLite + S3 adapters
│   ├── diff.py
│   └── cli.py               # `scrapo replay` / `scrapo diff`
├── policy/
│   ├── robots.py
│   ├── pii.py
│   ├── audit.py
│   └── geo.py
├── agent/
│   ├── mcp_server.py
│   └── tool_schemas.py
├── crawl/
│   ├── queue.py             # Persistent request queue (SQLite)
│   ├── scheduler.py         # Async scheduler with concurrency caps
│   └── dedup.py
└── cli.py                   # `scrapo` entry point
```

---

## 4. Phased Roadmap

### Phase 0 — Foundation (Week 1-2)
- Project skeleton, pyproject, ruff, mypy strict, pytest.
- Public API stubs (`scrape`, `crawl`, `extract`).
- SQLite snapshot store schema.
- Basic `httpx` T0 fetcher + markdown converter.

**Demo target:** `scrapo.scrape("https://docs.python.org/3/")` returns clean markdown.

### Phase 1 — Hybrid Extraction (Week 3-4)
- Pydantic schema → selector cache pipeline.
- Claude adapter with prompt caching.
- Selector-first, LLM fallback, self-healing loop.
- Model pinning enforcement.

**Demo target:** Extract product specs from 100 e-commerce pages; second run uses 0 LLM calls.

### Phase 2 — Tier Router + Browser (Week 5-6)
- Playwright integration (T2).
- Failure-signal detection (block fingerprints, 403/429, empty body).
- Auto-escalation T0 → T1 → T2.
- One proxy adapter (Bright Data or Oxylabs) for T3.

**Demo target:** Same scrape API works on a Cloudflare-protected site.

### Phase 3 — Crawler & Provenance (Week 7-8)
- Persistent request queue.
- Section-aware chunker with provenance tags.
- Cross-crawl dedup.
- `scrapo crawl` CLI.

**Demo target:** Crawl an entire docs site, output deduplicated chunks with selector-path provenance per chunk.

### Phase 4 — Replay + Policy (Week 9-10)
- `scrapo replay` and `scrapo diff` CLI.
- `robots.txt` gate, PII classifier, audit log.
- Geo-aware routing.

**Demo target:** Replay a 30-day-old run; diff shows two fields changed; audit log proves which proxy region was used.

### Phase 5 — Agent Mode + MCP (Week 11-12)
- T4 agent loop (login flows, multi-step).
- MCP server exposing all tools.
- Streaming JSON output.

**Demo target:** Claude Code agent calls `scrapo` via MCP, logs into a site, extracts a typed schema, returns the run_id for replay.

### Phase 6 — v1.0 Hardening (Week 13-14)
- Adapter coverage: all four major proxy vendors + 3 LLM providers.
- Benchmarks vs Firecrawl, Crawl4AI, Jina Reader on a fixed corpus.
- Docs site + tutorials.
- Public release on PyPI.

---

## 5. Repo-by-Repo Shortcoming → Scrapo Countermeasure

Every tool surveyed in the report and the specific weakness Scrapo addresses:

| Tool | Reported shortcoming | Scrapo countermeasure |
|---|---|---|
| **Firecrawl** | Credits expensive at scale; not strongest unblocker | T0/T1 path is free; T3 via BYO proxy |
| **Crawl4AI** | Self-managed anti-bot; reliability is operator's problem | Built-in tier router + adapter pattern |
| **ScrapeGraphAI** | LLM-mediated → non-deterministic, costly | Selector cache makes 2nd-run free and deterministic |
| **llm-scraper** | Not a crawler, no unblocker | Crawler queue + tier router built in |
| **Jina Reader** | Public URLs only; no auth; doesn't bypass anti-bot | Auth profiles, CDP session reuse, T3/T4 |
| **Stagehand** | Browser-only → slow at scale; needs hosted infra | HTTP-first; browser only when needed; fully self-hostable |
| **AgentQL** | Not a crawler; weak geo | Crawler + geo-aware router |
| **Browser Use** | Expensive vs deterministic; slow for bulk | T0-T2 handle bulk; T4 only when needed |
| **Playwright** | Low-level; no extraction or anti-bot | Built on Playwright but adds extraction + unblocker |
| **Puppeteer** | More manual than Playwright; WebMCP experimental | Use Playwright + first-class MCP |
| **Selenium** | Heavy, not LLM-native | Skip — Playwright is the better substrate |
| **Crawlee/Apify** | Output not LLM-friendly by default | Markdown + chunking + provenance native |
| **Scrapy** | No JS rendering, not LLM-native | Hybrid HTTP/browser router |
| **Bright Data** | Access-only, not AI ingestion | Used as proxy adapter; ingestion layered on top |
| **Zyte** | Usage-tier complexity, KYC | Borrow their **model pinning** — best feature in the market |
| **Scrapfly** | Credit economics; provenance still app's job | Provenance native to output |
| **Oxylabs** | LLM-output is secondary | LLM-output is the primary citizen |

---

## 6. Tech Stack Choices

- **Runtime:** Python 3.11+, `asyncio`, `uv` for package management.
- **HTTP:** `httpx` (async).
- **Browser:** `playwright` (async). Vision mode optional.
- **Schema:** `pydantic` v2.
- **Storage:** SQLite default (via `aiosqlite`); S3/Postgres adapters.
- **LLM clients:** `anthropic`, `openai`, `google-genai` — wired through one `LLMAdapter` protocol.
- **MCP:** the official `mcp` Python SDK.
- **Markdown:** custom converter built on `selectolax` (faster than BeautifulSoup) + `markdownify` for fallback.
- **Stealth:** `playwright-stealth` for T3.
- **Testing:** `pytest` + `pytest-asyncio` + recorded HTML fixtures (so tests don't hit the network).
- **Lint/type:** `ruff`, `mypy --strict`.

---

## 7. Open Questions / Risks

1. **LLM pinning UX** — exposing `(model_id, prompt_hash)` cleanly without leaking implementation. May need a "version bundle" abstraction.
2. **Selector derivation quality** — early prototypes will miss fields on complex pages. Plan for an offline "selector tuner" mode that lets users hand-correct cached selectors.
3. **Stealth arms race** — anti-bot vendors update faster than OSS stealth plugins. Mitigation: clean adapter boundary so commercial unblockers can be plugged in.
4. **Replay storage growth** — full HTML snapshots are heavy. Need configurable retention + compression.
5. **PII classifier accuracy** — regex catches the obvious; the optional model layer is opt-in only to keep cost predictable.
6. **Naming** — verify "scrapo" is available on PyPI before public release.

---

## 8. Success Metric for v1.0

A team should be able to:

```python
import scrapo

result = await scrapo.scrape(
    "https://example.com/products",
    schema=ProductSchema,
    budget=Budget(max_llm_calls=0, max_cost_usd=0.01),
)
```

…and get typed output with provenance, in <2s, with **zero LLM calls** on the second run, and a `run_id` they can replay six months later to prove what the page said today.
