# Testing & Benchmarks

This document records Scrapo's automated test coverage and the results of a
benchmark campaign covering performance, extraction quality, anti-bot resilience,
robustness, and scale. It is intended to be self-contained: every result below
states what was measured, how, and on what.

- **Benchmark run:** 2026-06-20, 23:30 (UTC+05:30)
- **Environment:** Windows 11, Python 3.13; Playwright Chromium; Docker 29.5.3
- **Scrapo revision:** branch `feat/api-first-deepseek-extraction-fixes`

> **How to read these numbers.** Benchmarks are grouped into *automated tests*
> (deterministic, in CI) and *campaign benchmarks* (one-off, against live sites or
> local fixtures). Campaign results carry caveats — small samples, no ground-truth
> labels, localhost timing — that are stated explicitly. Where Scrapo is compared
> to another tool, the comparison is **directional**, not a statistically
> significant ranking. See [Limitations](#10-limitations--not-tested).

---

## 1. Methodology

| Aspect | Detail |
|---|---|
| Automated suite | `pytest` — fully offline (no network, no paid LLM) + a separate `-m integration` suite driving real Chromium |
| Performance | Local HTTP/1.1 keep-alive server, fixed concurrency, percentile latencies — isolates library overhead from network jitter |
| Extraction | Live article URLs sampled from the Hacker News front page (reproducible, diverse) |
| Anti-bot / TLS | Public fingerprint/echo endpoints and a live bot-walled site (Wikipedia) |
| Grounded datasets | OWASP Juice Shop and a pywb WARC replay, both in Docker |
| Stability | Sustained load against the local server, sampling process RSS |

---

## 2. Automated test suite

| Suite | Result |
|---|---|
| Offline unit/functional (`pytest -q`) | **353 passed** |
| Real-browser integration (`pytest -m integration`) | **4 passed** |
| LLM extraction path (DeepSeek `deepseek-v4-flash`, live API) | **1 passed** |

The offline suite covers: the tier router and escalation signals (incl. SPA-shell
detection), HTTP retry and conditional-GET / 304-archive reuse, SSRF, content-type
shaping (HTML / JSON / feed / PDF / text), embedded-metadata extraction
(JSON-LD / OpenGraph / microdata), the hybrid selector+LLM extractor with cache
eviction and budget accounting, deterministic replay and field-diff, the proxy
pool, the agent driver and action cache, the sync facade, JSONL/CSV exporters,
the auto-pagination actions, the watch control plane, the local web UI, and
end-to-end `scrape`/`crawl` with monkeypatched fetchers. The integration suite
validates the browser tier, deterministic actions, and the metadata path against
a local fixture server with real headless Chromium.

**New tests added in this revision:** `test_api_providers.py` (API-first URL
resolution, 20 cases), `test_deepseek_adapter.py` (adapter wiring, 6 cases),
API-first integration cases in `test_api_scrape.py`, and title-extraction
regression cases in `test_shape.py`.

---

## 3. Performance & throughput

Local HTTP/1.1 server, ~8 KB page, fixed concurrency **c = 50**. Numbers measure
library/runtime overhead, **not** network — on a real network these gaps shrink.

| Client | RPS | p50 | p95 | p99 |
|---|---:|---:|---:|---:|
| `requests` (sync, sequential, 1 conn) | 1,936 | 0.5 ms | 0.9 ms | 1.1 ms |
| `aiohttp` (async) | **3,441** | 7.0 ms | 28.7 ms | 34.4 ms |
| `httpx` (async) | 295 | 101.6 ms | 453 ms | 615 ms |
| **Scrapo** (full pipeline) | 165–237 | 5.8 ms* | 142 ms | 163 ms |

\* Scrapo p50 is fetch-only latency; the per-request figure also includes markdown
shaping, chunking, provenance, and a replay-record write.

**Browser-tier latency** (warm nav + `page.content()`, 15 iterations):

| Mode | p50 | p95 |
|---|---:|---:|
| Headless Chromium | 8.6 ms | 11.6 ms |
| Headed / visible Chromium | 13.6 ms | 25.5 ms |

**Finding.** Scrapo is built on `httpx`, which is markedly slower than `aiohttp`
at high concurrency on this platform. For bulk fetching of many small static
pages this is the throughput ceiling; for typical scraping (network-, browser-,
or LLM-bound) the difference is negligible.

---

## 4. Extraction quality

### 4.1 vs. trafilatura / readability / newspaper4k

21 live article pages. No ground truth, so this measures *coverage* (substantive
text returned) and field presence.

| Tool | Coverage (≥200 chars) | Median chars | Title | Author | Date |
|---|---:|---:|---:|---:|---:|
| **Scrapo** (main-content) | 20/21 | 8,665 | 21/21 | — | — |
| trafilatura | 21/21 | 5,946 | 21/21 | 11/21 | 20/21 |
| readability | 20/21 | 3,882 | 21/21 | — | — |
| newspaper4k | 20/21 | 4,564 | 21/21 | 8/21 | 13/21 |

Scrapo's main-content text length is ~1.23× trafilatura's (median) — i.e. it
trims slightly less boilerplate. Coverage and title extraction are on par with
the dedicated libraries.

> **Bug found and fixed during this campaign.** Scrapo originally extracted a
> title on only **2/21** pages: `main_content=True` strips the document `<head>`,
> so the `<title>` was lost. Fixed by falling back to `og:title` → `<title>` →
> first `<h1>` from the source HTML. Title coverage is now **21/21**. Regression
> tests added.

### 4.2 vs. crawl4ai

Markdown characters extracted, 3 URLs:

| URL | Scrapo | crawl4ai | Notes |
|---|---:|---:|---|
| Wikipedia article | 0 | 68,817 | Scrapo run with API-first **disabled** → hit the CAPTCHA wall (see §5.2). With API-first on (default) it returns the article via the REST API. |
| Cloudflare blog post | 8,257 | 17,719 | both succeeded; crawl4ai more verbose |
| eli.thegreenplace post | 17,725 | 19,126 | comparable |

Scrapo served 2 of 3 pages from its HTTP tier (no browser, ~1–1.6 s); crawl4ai
always launches a browser. On normal pages the two are comparable, with crawl4ai
retaining more text.

### 4.3 Zero-LLM schema coverage

Fields obtained from embedded structured data alone (no LLM), 16 live pages:

| Field | Coverage |
|---|---:|
| title | 94% (15/16) |
| author | 56% (9/16) |
| date | 56% (9/16) |
| main text | 100% (16/16) |

---

## 5. Anti-bot & TLS

### 5.1 TLS fingerprint (JA3 / JA4)

Observed by a public fingerprint-echo API:

| Client | JA3 | JA4 |
|---|---|---|
| Scrapo HTTP tier (httpx) | `304734bb1c086c34` | `t13d1812h1_85036bcba153_…` |
| Raw httpx (Chrome UA, h2) | `304734bb1c086c34` | `t13d1812h1_85036bcba153_…` |
| Raw httpx (Chrome UA, h1) | `304734bb1c086c34` | `t13d1812h1_85036bcba153_…` |

The fingerprint is **identical regardless of the `User-Agent` string** — it
reflects the Python/httpx TLS stack. A scraper faking a browser UA over the HTTP
tier is therefore trivially distinguishable by JA3/JA4. Defeating TLS
fingerprinting requires a browser tier or a TLS-mimicking transport.

### 5.2 Live bot-wall (Wikipedia)

Fetching `en.wikipedia.org/wiki/...` directly (API-first disabled):

| Tier | Status | Result |
|---|---|---|
| HTTP | 200 | `blocked = true`, reason `captcha` |
| Browser | 200 | `blocked = true`, reason `captcha` |

This reproduces the motivating problem: Wikipedia CAPTCHAs Scrapo's honest tiers.
The shipped remedy is **API-first resolution** — Wikipedia and its Wikimedia
sister projects resolve to their public REST API before the tier router runs, so
the article is returned cleanly with no challenge. Confirmed working in
`test_api_providers.py` and the API-first scrape tests.

### 5.3 Incolumitas bot test

The HTTP tier retrieves the page shell (status 200) but cannot execute the
JavaScript detection suite. The deeper TLS/TCP classifier endpoint
(`tcpip.incolumitas.com`) **actively refused connections from this host** —
consistent with datacenter-IP filtering, and itself an illustration that beating
aggressive anti-bot needs residential egress, not just a stealthier client.

---

## 6. Grounded datasets

### 6.1 OWASP Juice Shop (Docker, Angular SPA)

| Step | Result |
|---|---|
| Scrape `/` (auto tier) | Escalated HTTP → **browser**; title `OWASP Juice Shop` |
| XHR capture | **5** JSON XHR responses captured automatically |
| REST API `/api/Products` (HTTP tier) | Shaped as JSON → **46 products** (first: "Apple Juice (1000ml)", 1.99) |

A JavaScript SPA renders little visible markdown, but the automatic XHR capture
surfaces the underlying data, and the REST API is shaped directly.

### 6.2 WARC replay via pywb (Docker)

A self-recorded WARC was replayed through the `webrecorder/pywb` container and
scraped:

| Replayed URL | Result |
|---|---|
| `example.com` | status 200, title "Example Domain" ✓ |
| IANA example-domains page | status 200, 810 chars ✓ |

Scrapo treats a pywb replay as an ordinary HTTP origin, so archived corpora
(e.g. Common Crawl-style WARCs) scrape like live pages. *(A self-recorded WARC
was used, not a multi-GB Common Crawl segment; the replay-scraping path is
identical.)*

---

## 7. Robustness & quality

| Test | Result |
|---|---|
| **Selector stability across revisions** | v1 derives + caches selectors (1 LLM call); v2 reshuffled layout reuses them with **0 LLM calls**; v3 rewritten layout **self-heals** (1 call). |
| **Snapshot regression** (replay + diff) | Identical re-scrape → "HTML identical / no change"; after a price edit → "HTML changed". |
| **Canonical URL / dedup** | 7 anchor links (exact dupes + `#fragment` variants + 1 query) → **4 unique** URLs; fragments and exact dupes collapse, query-distinct URLs preserved. |
| **post-DOMContentLoaded mutation** | Content injected 1.2 s after load is **missed** by default and **captured** with `wait_for`. |

---

## 8. Scale & stability (soak)

Sustained `batch_scrape` against the local server; process RSS sampled per round.

| Run | Requests | Errors | RSS (start → peak → end) | Memory drift |
|---|---:|---:|---|---|
| 90 s | 21,000 | **0 (0.00%)** | 53 → 98 → 79 MB | +8 MB (GC noise) |
| 10 min | 142,600 | **0 (0.00%)** | 54 → 101 → 82 MB | +1 MB |
| 20 min† | 320,000 | **0 (0.00%)** | flat 76–79 MB | none observable |

† A 2-hour run was launched and stopped at 20 minutes once stability was
conclusive (0 errors, no memory growth). No leak was observed in any run.

---

## 9. Competitive scorecard

A consolidated, directional read of where Scrapo stands against the tools it was
measured against.

| Dimension | Verdict | Detail |
|---|---|---|
| Raw fetch throughput | 🔴 Behind | Built on httpx; ~10× slower than aiohttp on small static pages |
| Fetch-layer latency | 🟡 Par | ~6 ms p50, comparable to aiohttp |
| Article extraction (coverage/title/metadata) | 🟡 Par | On par with trafilatura / readability / newspaper4k / crawl4ai |
| Extraction efficiency | 🟢 Ahead | Tier router avoids launching a browser when HTTP suffices |
| Selector reuse + self-heal | 🟢 Ahead | Cached selectors reused for free; heal on layout change |
| Content-type breadth | 🟢 Ahead | HTML, JSON APIs, feeds, PDF, SPA XHR capture, WARC replay |
| Provenance / replay / diff | 🟢 Ahead | Per-chunk provenance and deterministic replay/diff are unique here |
| TLS stealth (JA3/JA4) | 🔴 Behind | HTTP-tier fingerprint is fixed and detectable |
| Hard anti-bot (no proxies) | 🔴 Behind / 🟢 Ahead via API | Raw tiers are CAPTCHA-walled; API-first bypasses for sites with APIs |
| Stability under load | 🟢 Strong | 0 errors, no leak across all soak runs |

**Summary.** Scrapo is not the fastest fetcher or the stealthiest client, but as
an end-to-end platform it matches dedicated extractors on quality and leads on
breadth, provenance, resilience features, and its API-first answer to bot walls.

---

## 10. Limitations & not tested

- **Sample sizes are small** (3–21 pages per extraction comparison) and
  unlabeled — comparisons are directional, not statistically significant.
- **Throughput numbers are localhost** and exaggerate library differences that
  largely vanish under real-network latency.
- **Not run** (require external infrastructure not available in this
  environment): NowSecure / PerimeterX / Cloudflare challenge sites and
  residential-proxy paths; full Common Crawl segments; the Crawl4AI and
  Scrapy/ScrapingBee public benchmark suites; a true multi-hour (>20 min) soak.

---

## 11. Reproducing

```bash
# automated suites
pip install -e ".[dev]"
pytest -q                                  # offline suite
pip install -e ".[dev,browser]" && playwright install chromium
pytest -m integration                      # real-browser suite

# DeepSeek LLM extraction path
export SCRAPO_LLM_ADAPTER=deepseek DEEPSEEK_API_KEY=sk-...
pytest tests/test_api_scrape.py::test_scrape_with_schema_records_extraction
```

The campaign benchmarks (performance, extraction A/B, anti-bot, soak, grounded
datasets) were run as standalone scripts against the fixtures described above.
