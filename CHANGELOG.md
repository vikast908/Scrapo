# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/vikast908/Scrapo/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/vikast908/Scrapo/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/vikast908/Scrapo/releases/tag/v0.1.0
