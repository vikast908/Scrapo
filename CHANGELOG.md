# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-10

Initial public release.

### Added

- **Public async API** — `scrapo.scrape`, `scrapo.extract`, `scrapo.crawl`.
- **5-tier access router** — T0 `HTTP` → T1 `HTTP_SESSIONED` → T2 `BROWSER` (Playwright) → T3 `BROWSER_STEALTH` → T4 `AGENT`, with automatic escalation on anti-bot fingerprints (Cloudflare/Akamai/PerimeterX/DataDome/Distil), HTTP `403/429/503`, empty bodies, or missing required schema fields. Per-call budget and `force_tier` controls.
- **Proxy adapters** — built-in Bright Data, Oxylabs, Scrapfly, and Zyte adapters; `ProxyAdapter` protocol for custom vendors; `SCRAPO_PROXY_ADAPTER` / `SCRAPO_GEO` defaults.
- **Hybrid extractor** — selector-cache-first, LLM fallback with the Pydantic schema; the LLM returns both the JSON payload and per-field CSS selectors, which are verified against the live HTML before being cached and self-healed on drift.
- **LLM adapters** — Anthropic (default, with prompt caching on the schema block), OpenAI, Google Gemini, and an offline mock adapter.
- **Model pinning** — `PinnedModel` plus `strict_pin=True` to refuse extraction when the configured model doesn't match the pin, preventing silent model drift.
- **Document shaper** — selectolax-based HTML → Markdown, heading-aware chunking, and per-chunk provenance (URL, selector path, byte range, heading trail, content hash); cross-crawl chunk deduplication.
- **Deterministic replay** — every fetch persists raw HTML, headers, and screenshots alongside the typed extraction in a SQLite store; `scrapo replay` re-extracts from the archive with no network, and `scrapo diff` produces a field-level diff with notes when the model or schema changed.
- **Compliance layer** — optional `robots.txt` gate with per-origin caching, a regex PII classifier (email, phone, SSN, credit card with Luhn check, IPv4, IBAN, passport), a geo policy with an EU-only preset plus custom allow/deny lists, and an append-only JSONL audit log of every scrape, block, geo violation, and PII detection.
- **Crawling** — persistent SQLite request queue and an async scheduler with per-host crawl-delay (from `robots.txt`) and URL deduplication; `max_depth`, `max_pages`, and `same_host_only` controls.
- **MCP server** — exposes `scrapo_scrape`, `scrapo_crawl`, `scrapo_replay`, `scrapo_diff`, and `scrapo_list_runs` to any MCP-compatible client over stdio.
- **CLI** (`scrapo`) — `scrape`, `crawl`, `list`, `replay`, `diff`, `audit`, `adapters`, `serve`, and `mcp` commands.
- **Local browser UI** — `scrapo serve` runs a small HTTP server with markdown / chunks / JSON views of a scrape; scrapes are serialized so concurrent requests don't race on the SQLite stores.
- **Configuration** — every default is overridable via `SCRAPO_*` environment variables; data lives under a platform user-data directory by default.
- **Tests** — a fully offline test suite (no network, no paid LLM) covering signals, shape, extract, replay, policy, dedup, queue, router, adapters, config, the local web UI, and end-to-end scrapes with monkeypatched fetchers.

### Notes

- The `robots.txt` gate is **opt-in**: set `SCRAPO_RESPECT_ROBOTS=1` (or `Config(respect_robots=True)`) to enable it. You are responsible for complying with each site's terms of use and applicable law.
- Alpha status: the public API and core subsystems are stable, but the T4 agent driver, full action caching, an S3 snapshot adapter, and a hosted control plane are intentionally lightweight or not yet implemented.

[Unreleased]: https://github.com/vikast908/Scrapo/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vikast908/Scrapo/releases/tag/v0.1.0
