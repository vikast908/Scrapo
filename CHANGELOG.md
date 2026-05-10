# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-05-11

Feature release: conditional requests, change tracking (`watch()`), and a
streaming crawl. Turns the replay + field-diff foundation into a "tell me what
changed" workflow, and makes re-scrapes cheap.

### Added

- **Conditional GET** (`scrapo.access.http_tier`): re-scraping a URL the HTTP tier fetched before now sends `If-None-Match` / `If-Modified-Since` from the prior run's validators. On a `304 Not Modified`, `scrape()` rebuilds the page from the archived snapshot (and skips the LLM: the selector cache makes re-extraction free), pointing the new run at the same snapshot instead of writing a duplicate. `FetchResult` / `ScrapeResult` / `RunRecord` gained a `not_modified` flag; `FetchResult` gained `etag` / `last_modified` / `validators()`; the `runs` table gained `etag` / `last_modified` / `not_modified` columns (auto-migrated on existing DBs with `ALTER TABLE`). On by default; `Config(conditional_requests=False)` or `SCRAPO_CONDITIONAL_REQUESTS=0` disables it. New `Conditional` type and `ReplayStore.last_run(url)`.
- **`watch()` / `Watch.refresh()` (`scrapo.watch`)**: `watch(url, schema=...)` does an initial scrape and hands back a `Watch`; `await w.refresh()` re-scrapes (conditional GET kicks in automatically) and returns a `ChangeSet`: `changed` / `not_modified` flags, the field-level `diff` (the existing `DiffReport`), and the fresh `ScrapeResult`. In-process only (the run history lives in the replay store); persisting a watch *list* with a scheduler is a deployable-service concern and stays out of scope. `watch`, `Watch`, `ChangeSet` are re-exported from `scrapo`.
- **Streaming crawl** (`scrapo.crawl_stream`): `async for page in crawl_stream(seeds, ...)` yields each `ScrapeResult` as it completes instead of buffering the whole crawl; breaking out early stops the crawl and tears down the shared browser. `crawl()` stays as the buffered convenience.
- **CLI / MCP**: `scrapo scrape <url> --diff-last` prints a field-level diff against the previous recorded run of that URL (and shows `not-modified` when a 304 was reused); the `scrapo_scrape` MCP tool gained a `diff_last` argument that returns the diff alongside the result.
- New config / env var: `conditional_requests` / `SCRAPO_CONDITIONAL_REQUESTS`.

### Not in this release

- A hosted control plane (a scheduler that runs and persists a list of watches, sends alerts, and gives you a web console) is a deployable service rather than a library feature and stays out of scope.

## [0.6.0] - 2026-05-10

Roadmap release: deeper proxy rotation with per-endpoint health tracking. This
closes out the library roadmap; the only remaining item, a hosted control
plane, is a separate deployable service rather than a library feature.

### Added

- **Rotating proxy pool with health checks** (`scrapo.access.proxy_pool.ProxyPool`): give Scrapo a list of proxy URLs (`Config(proxy_urls=[...])` or `SCRAPO_PROXY_URLS="http://a,http://b"`) and the `TierRouter` round-robins across them, skipping any that are in cooldown. `ProxyPool` implements the `ProxyAdapter` protocol, so it slots in exactly where a vendor adapter would; pass `upstream=<adapter>` to fall back to a managed gateway when every static endpoint is parked (otherwise it falls back to a direct connection rather than a known-bad proxy). `ProxyConfig` gained a `key` field so an endpoint can be tracked; `ProxyPool.stats()` returns per-endpoint success/failure/cooldown counters for introspection.
- **Proxy health feedback loop**: the HTTP and browser tiers now report every fetch's outcome back to a pool-like adapter (`report_outcome`). An HTTP 4xx auth/rate-limit code or an anti-bot fingerprint (Cloudflare, DataDome, …) is an IP-level block and parks that proxy immediately for `proxy_cooldown_seconds`; a transient 5xx, network error, or empty body counts toward `max_failures` (default 3) before parking; a clean fetch resets the streak. Credentials are stripped from proxy URLs before they hit the logs.
- New config / env vars: `proxy_urls` / `SCRAPO_PROXY_URLS`, `proxy_cooldown_seconds` / `SCRAPO_PROXY_COOLDOWN`. `ProxyPool` is re-exported from `scrapo.access`.

### Changed

- A passed-in `proxy_adapter` (or a registered `SCRAPO_PROXY_ADAPTER`) still takes precedence; the static `ProxyPool` is only auto-built when no adapter is otherwise configured. To combine them, construct `ProxyPool(urls, upstream=<adapter>)` yourself and pass it to `TierRouter` / `scrape()`.

## [0.5.0] - 2026-05-10

Roadmap release: Stagehand-style action caching for the Tier-4 agent driver, the
last open item from the 0.4.0 list. (A hosted control plane stays out of scope; it
is a deployable service, not a library feature.)

### Added

- **Agent action caching** (`scrapo.access.action_cache.ActionCache`): the Tier-4 agent driver now records the ordered actions it took to reach a goal on a host. Later runs with the same goal replay that script directly (zero LLM tokens) and only fall back to the model if a replayed step no longer applies (its element is gone, a navigation fails, etc.). Recordings are keyed by `(host, goal_hash)` in `agent_actions.sqlite`, with a per-key `failure_count` so a stale script is evicted after `cache_max_failures` (default 2) failed replays instead of being retried forever. Each recorded click/type carries a best-effort durable CSS selector plus the element's text/tag, so replay survives changing snapshot indices; `AgentTier` wires the cache in from config. On by default; disable with `Config(agent_action_cache=False)` or `SCRAPO_AGENT_ACTION_CACHE=0`. The snapshot JS the driver injects now also returns a `sel` (CSS path) for each element, and `LLMAgentDriver.run`'s result dict gains a `replayed` flag.
- New config / env var: `agent_action_cache` / `SCRAPO_AGENT_ACTION_CACHE`; new `Config.action_cache_db` path.

### Changed

- `AgentDriver.run` (the Tier-4 driver protocol) gained a keyword-only `cache: ActionCache | None = None` parameter; `AgentTier` passes its configured cache through. A custom driver that ignores action caching can leave the parameter unused, but must accept it.

### Not in this release

- A hosted control plane is a deployable service rather than a library feature and is out of scope here.

## [0.4.0] - 2026-05-10

Roadmap release: content-type routing, sitemap + pagination, in-browser request
interception, a reference agent driver, and a pluggable (S3-capable) snapshot store.

### Added

- **Content-type routing** (`scrapo.shape.dispatch`): not every URL is an HTML page. JSON / JSON-LD is parsed and exposed on `result.data` (with the pretty-printed body as markdown); RSS / Atom feeds become a markdown list of entries with the items on `result.data`; PDFs are text-extracted via the `[pdf]` extra (`pip install "scrapo[pdf]"`); `text/plain` is passed through verbatim. `result.kind` says which path ran (`html` / `json` / `feed` / `pdf` / `text`). The HTTP tier keeps binary bodies as `FetchResult.raw_content`, and the router no longer mislabels a binary payload as "thin".
- **Sitemap ingestion + pagination** in `crawl()`: `crawl(..., use_sitemap=True)` seeds from each origin's `sitemap.xml` (following one layer of sitemap index); every crawl now also follows `rel="next"` pagination links (at the same depth, still bounded by `max_pages`). `scrapo.crawl.sitemap.discover_sitemap_urls` is the standalone helper.
- **In-browser request interception** (`BrowserTier`): images / fonts / media / stylesheets are blocked by default for faster page loads (`browser_block_resources`), and JSON XHR/fetch responses the page makes are surfaced on `FetchResult.captured_json` / `ScrapeResult.captured_json` (`browser_capture_xhr`), so you can read the site's own API instead of scraping rendered DOM.
- **Reference Tier-4 agent driver** (`scrapo.access.agent_drivers.LLMAgentDriver`): snapshot the visible interactive elements, ask the LLM for one action (click / type / scroll / goto / done), execute it, repeat up to a step limit. Enable with `TierRouter(config, agent_driver=LLMAgentDriver())` or `SCRAPO_AGENT_DRIVER=llm`. Works with any configured LLM adapter.
- **Pluggable snapshot storage** (`scrapo.replay.snapshots`): the replay store now writes page bodies through a `SnapshotStore`. `snapshot_backend="local"` (default) keeps files under the data dir; `snapshot_backend="s3://bucket/prefix"` (or `SCRAPO_SNAPSHOT_BACKEND`) stores them in S3 via the `[s3]` extra. `ReplayStore` accepts a custom store for anything else.
- New extras: `[pdf]` (`pypdf`) and `[s3]` (`boto3`); `moto[s3]` added to the dev extra for offline S3 tests.
- New config / env vars: `SCRAPO_SNAPSHOT_BACKEND`, `SCRAPO_BROWSER_BLOCK_RESOURCES`, `SCRAPO_BROWSER_CAPTURE_XHR`, `SCRAPO_AGENT_DRIVER`.

### Fixed

- Code-fence stripping in the extractor and the Anthropic adapter handled `` ```json ... ``` `` (a closing fence) incorrectly, throwing the content away; it now takes the fenced block's body. The agent driver's action parser uses the corrected logic.

### Not in this release

- "Full Stagehand-style action caching" (recording and safely replaying agent action sequences) builds on the new agent driver and is left for a future release.
- A hosted control plane is a deployable service rather than a library feature and is out of scope here.

## [0.3.0] - 2026-05-10

Capability release: typed results, list/nested extraction, and a reused browser.

### Added

- **Typed result objects** (`scrapo.results`): `scrape()` returns `ScrapeResult`, `crawl()` returns `CrawlResult`, and `extraction` on a result is an `ExtractionView`. They are Pydantic models, so you get attribute access (`result.markdown`), validation, and `result.model_dump()` for serialization. They also support `result["key"]`, `result.get("key", default)`, and `"key" in result` so code written against the 0.1/0.2 dict shape keeps working unchanged.
- **List / nested extraction**: schema fields typed `list[SomeBaseModel]` are now extracted as repeated DOM elements. The LLM returns a container selector plus per-subfield selectors (`{"products": {"__list__": "ul.grid > li", "name": "h3", "price": ".price"}}`), those are verified against the live page, cached, and replayed on later runs with zero LLM tokens, exactly like scalar fields. `scrapo.extract.schema.list_fields()` exposes the detection.
- **Browser-context pooling** (`scrapo.access.browser_pool.BrowserPool`): a `TierRouter` now lazily launches one Chromium and reuses it across fetches (proxy settings move to the context level so a single browser serves rotating proxies). A crawl no longer cold-launches a browser per page. `TierRouter.aclose()` tears it down; `scrape()` closes the router it creates, and `crawl()` shares one router across all pages. `scrape()` gained a `router=` keyword for callers that want to reuse one explicitly.
- The flaky `playwright-stealth` integration is applied to the page before navigation (instead of via a context event that raced the first page) and tries both the old and new plugin entry points.

### Changed

- `scrape()` / `crawl()` return Pydantic models instead of plain `dict`. Dict-style read access still works; `isinstance(result, dict)` does not. The MCP server serializes results with `model_dump(mode="json")`.

## [0.2.0] - 2026-05-10

Hardening release: makes the "cost-aware" and "production crawling" claims real,
adds an SSRF guard, and tightens the local UI. No breaking changes to the public
`scrape` / `extract` / `crawl` signatures, but `scrape()` blocked results now use
`block_reason` consistently (the legacy `reason` key is gone).

### Added

- **SSRF guard** (`scrapo.security`): every fetch target is checked before a request goes out. Loopback, link-local (including the cloud metadata address `169.254.169.254`), private RFC 1918 / ULA ranges, and well-known local hostnames are refused. Opt out with `Config(allow_private_hosts=True)` or `SCRAPO_ALLOW_PRIVATE_HOSTS=1`. Crawl link discovery applies the same filter and skips obvious binary URLs.
- **Cost accounting and budget enforcement**: LLM cost from the adapter is now recorded on `RunRecord.cost_usd` and surfaced in scrape results; `Budget(max_llm_calls=...)` is enforced by the extractor (it returns `method="none"` instead of calling the model when the budget is spent).
- **SPA-shell escalation signal** (`signals.is_spa_shell`): an unrendered single-page-app shell (lots of `<script>`, almost no rendered text) now escalates straight from the HTTP tier to a browser instead of being mislabeled "thin".
- **Bounded HTTP retries**: the HTTP tier retries `429 / 5xx` responses and transport errors with exponential backoff and jitter before the router escalates (`SCRAPO_HTTP_RETRIES`, default `2`).
- **Snapshot PII redaction**: `Config(redact_snapshots=True)` / `SCRAPO_REDACT_SNAPSHOTS=1` redacts detected PII from the stored HTML snapshot, markdown, and chunks.
- **`scrapo serve` hardening**: the local UI validates the `Host` header against an allowlist when bound to loopback (anti DNS-rebinding) and warns loudly if you bind a public interface.
- **structlog configuration** (`scrapo.logging`): the CLI and MCP server now configure logging once at startup; `SCRAPO_LOG_LEVEL` and `SCRAPO_LOG_FORMAT` (`console` or `json`) control it.
- **`py.typed` marker**: scrapo now ships its type information to downstream users.
- Project metadata: `[project.urls]`, Python 3.13 classifier, `Typing :: Typed` classifier, `pytest-cov` in the dev extra, and a `[tool.coverage]` section. GitHub Actions CI runs ruff, mypy, and pytest on Python 3.11 to 3.13.

### Changed

- **Selector cache is keyed by host, not registered domain**, so `blog.example.com` and `shop.example.com` no longer share (and corrupt) each other's selectors.
- **Stale selectors are evicted automatically**: a cache entry that fails validation `STALE_SELECTOR_THRESHOLD` (3) times in a row is dropped so the next run re-derives it via the LLM instead of failing forever. A successful run resets the failure counter.
- **All SQLite stores (replay, selector cache, crawl queue) now open in WAL mode with a busy timeout**, so concurrent crawl workers stop hitting `database is locked`.
- `RequestQueue.claim()` returns the post-claim row state (status, attempts, claimed_at) instead of the stale pre-update row.
- The crawl scheduler no longer reaches into a private asyncio internal to detect in-flight work; it tracks outstanding tasks explicitly.
- HTML snapshots are gzipped at level 6 (was 9) for a much better speed/size tradeoff.
- `scrape()` short-circuits when the access router exhausts escalation on a blocked fetch (returns a `blocked` result instead of shaping empty HTML).

### Removed

- Dependency on `tldextract` (the selector cache now uses the URL host directly, which also removes its first-use network fetch of the public-suffix list).
- Dependency on `tenacity` (the HTTP tier has its own small retry loop).
- The legacy `reason` key from `scrape()` blocked results; use `block_reason`.

### Fixed

- `describe_block_reason` renders a missing geo region as "unknown region" instead of the literal string "None".

## [0.1.0] - 2026-05-10

Initial public release.

### Added

- **Public async API**: `scrapo.scrape`, `scrapo.extract`, `scrapo.crawl`.
- **5-tier access router**: T0 `HTTP` to T1 `HTTP_SESSIONED` to T2 `BROWSER` (Playwright) to T3 `BROWSER_STEALTH` to T4 `AGENT`, with automatic escalation on anti-bot fingerprints (Cloudflare/Akamai/PerimeterX/DataDome/Distil), HTTP `403/429/503`, empty bodies, or missing required schema fields. Per-call budget and `force_tier` controls.
- **Proxy adapters**: built-in Bright Data, Oxylabs, Scrapfly, and Zyte adapters; `ProxyAdapter` protocol for custom vendors; `SCRAPO_PROXY_ADAPTER` / `SCRAPO_GEO` defaults.
- **Hybrid extractor**: selector-cache-first, LLM fallback with the Pydantic schema; the LLM returns both the JSON payload and per-field CSS selectors, which are verified against the live HTML before being cached and self-healed on drift.
- **LLM adapters**: Anthropic (default, with prompt caching on the schema block), OpenAI, Google Gemini, and an offline mock adapter.
- **Model pinning**: `PinnedModel` plus `strict_pin=True` to refuse extraction when the configured model does not match the pin.
- **Document shaper**: selectolax-based HTML to Markdown, heading-aware chunking, and per-chunk provenance (URL, selector path, byte range, heading trail, content hash); cross-crawl chunk deduplication.
- **Deterministic replay**: every fetch persists raw HTML, headers, and screenshots alongside the typed extraction in a SQLite store; `scrapo replay` re-extracts from the archive with no network, and `scrapo diff` produces a field-level diff with notes when the model or schema changed.
- **Compliance layer**: optional `robots.txt` gate with per-origin caching, a regex PII classifier (email, phone, SSN, credit card with Luhn check, IPv4, IBAN, passport), a geo policy with an EU-only preset plus custom allow/deny lists, and an append-only JSONL audit log.
- **Crawling**: persistent SQLite request queue and an async scheduler with per-host crawl-delay (from `robots.txt`) and URL deduplication; `max_depth`, `max_pages`, and `same_host_only` controls.
- **MCP server**: exposes `scrapo_scrape`, `scrapo_crawl`, `scrapo_replay`, `scrapo_diff`, and `scrapo_list_runs` to any MCP-compatible client over stdio.
- **CLI** (`scrapo`): `scrape`, `crawl`, `list`, `replay`, `diff`, `audit`, `adapters`, `serve`, and `mcp` commands.
- **Local browser UI**: `scrapo serve` runs a small HTTP server with markdown / chunks / JSON views of a scrape.
- **Configuration**: every default is overridable via `SCRAPO_*` environment variables; data lives under a platform user-data directory by default.
- **Tests**: a fully offline test suite (no network, no paid LLM).

### Notes

- The `robots.txt` gate is opt-in: set `SCRAPO_RESPECT_ROBOTS=1` (or `Config(respect_robots=True)`) to enable it. You are responsible for complying with each site's terms of use and applicable law.
- Alpha status: the public API and core subsystems are stable, but the T4 agent driver, full action caching, an S3 snapshot adapter, and a hosted control plane are intentionally lightweight or not yet implemented.

[Unreleased]: https://github.com/vikast908/Scrapo/compare/v0.4.0...HEAD
[0.4.0]: https://github.com/vikast908/Scrapo/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/vikast908/Scrapo/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/vikast908/Scrapo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vikast908/Scrapo/releases/tag/v0.1.0
