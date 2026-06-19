# Contributing to Scrapo

Thanks for your interest. Bug reports, feature requests, and pull requests are all welcome.

This guide covers how to set up a dev environment, the quality gates every PR has to pass, the house rules that keep the codebase consistent, where things live, and how to add the most common kinds of extension (an LLM adapter, a proxy adapter).

## Dev setup

```bash
git clone https://github.com/vikast908/Scrapo
cd Scrapo
python -m venv .venv
. .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium   # only needed for the browser tiers
```

`.[dev]` pulls in everything the test suite and the quality gates need: `pytest`, `pytest-asyncio`, `pytest-httpx`, `pytest-cov`, `respx`, `ruff`, `mypy`, plus `pypdf`, `boto3`, and `moto[s3]` so the PDF and S3 paths can be exercised offline.

If you want to run the full feature surface locally (browser tiers, the LLM adapters, the MCP server, PDF parsing, S3 snapshots), install the umbrella extra:

```bash
pip install -e ".[all]"       # browser + anthropic + openai + gemini + mcp + pdf + s3
playwright install chromium   # the browser tiers need a real Chromium
```

Each extra can also be installed on its own: `browser`, `anthropic`, `openai`, `gemini`, `mcp`, `pdf`, `s3`. The base `pip install scrapo` ships with no extras, and that has to keep working (see the optional-dependencies house rule below).

## Quality gates

CI runs all three of these on Python 3.11 to 3.13. **All three must pass before a PR can merge.** Run them locally first:

```bash
pytest -q          # ~280 tests, fully offline
ruff check .       # lint
mypy scrapo/       # strict type check
```

### Tests

The test suite is fully offline by design: **no test may hit the network or a paid LLM.** Use `respx` / `pytest-httpx` to stub HTTP, monkeypatch the tiers, or use the `mock` LLM adapter. There are roughly 280 tests covering signals (including SPA-shell detection), the SSRF guard, the HTTP retry path, conditional GET / 304-archive reuse, `watch()` change tracking and `crawl_stream`, shape, extract (cache eviction, budget, cost), replay (and the schema migration), policy, dedup, queue, router, the proxy adapters and rotating pool, the Tier-4 agent driver and its action cache, the local web UI, config, and end-to-end scrape with monkeypatched fetchers. If you add a feature, add a test.

### Lint

The ruff rule set is configured in `pyproject.toml`: the selected groups are `E, F, I, B, UP, N, ASYNC, S, RET, SIM, TID`, and the line length is 100. A few rules are intentionally ignored (`E501`, `S101`, `ASYNC240`); see the comments in `pyproject.toml` for why. Tests carry their own per-file ignores (`S105`, `S106`, `S311`).

### Types

`mypy scrapo/` runs in **strict** mode (`pydantic.mypy` plugin enabled). It must pass with no new `# type: ignore`. Add one only with a specific error code and a short comment explaining it.

## House rules

- **Type everything.** mypy strict has to pass. Public functions, parameters, and return types all carry annotations.
- **Optional dependencies stay optional.** Anything under `[project.optional-dependencies]` (Playwright, the LLM SDKs, `mcp`, `pypdf`, `boto3`) must be imported lazily inside the function that uses it, so `pip install scrapo` works with no extras. Return a `blocked` `FetchResult` (or raise a clear `ImportError`) when an extra is missing.
- **Don't block the event loop.** Prefer `asyncio.to_thread` for blocking I/O inside async code rather than calling synchronous I/O directly on the loop.
- **No bare exception handling.** Never broaden a handler to `except:` or `except BaseException`. Use `except Exception`, and reach for `# noqa: BLE001` only when you genuinely need a broad catch and can give a reason in a comment.
- **All SQLite access goes through `scrapo/_db.py`.** New code that touches SQLite uses the pooled `connect()` helper there (WAL mode, busy timeout, guarded init). Do not open `aiosqlite` / `sqlite3` connections directly.
- **Every new fetch target passes through the SSRF guard.** Any code path that issues an outbound request routes through `security.check_url` / `security.safe_get` so loopback, link-local, and private ranges stay blocked by default. This includes URLs an LLM or agent chooses at runtime.
- **Keep the public API backward compatible.** `scrape` / `extract` / `crawl` / `crawl_stream` / `watch` are the public surface. Add new behavior behind a new optional keyword argument (or a `Config` field) with a sensible default. Don't change existing signatures.
- **No new network calls at import time.** (This is why `tldextract` was dropped.)
- **Touch the docs.** New config goes in the README config table; user-visible changes go in `CHANGELOG.md` under `[Unreleased]`.
- **Markdown style:** no em-dashes in docs. Use a colon, a comma, parentheses, or rewrite the sentence.

## Project layout

A quick map of the `scrapo/` subpackages so you know where a change belongs:

```
scrapo/
├── access/      # tier router + the 5 tiers + proxy adapters/pool + agent driver & action cache
├── extract/     # hybrid selector + LLM extraction, selector cache, model pinning
├── shape/       # markdown conversion + heading chunker + readability + content-type dispatch
├── replay/      # snapshot store (local / S3) + replay metadata + field-level diff
├── policy/      # robots, PII (flag or redact), geo allow/deny, append-only audit
├── crawl/       # persistent queue + async scheduler + dedup + sitemap + mapper + batch
└── agent/       # MCP server + tool schemas
```

In a bit more detail:

- **`access/`** is the tier router (`router.py`) plus the tiers themselves (HTTP, sessioned HTTP, browser, stealth browser, agent), the pooled browser, request interception, the agent driver and action cache, and the proxy adapters and rotating pool.
- **`extract/`** is the hybrid selector + LLM extractor: scalar and `list[Model]` fields, the host-keyed selector cache, and model pinning.
- **`shape/`** turns HTML into markdown, chunks it by heading with per-chunk provenance, applies readability, and dispatches by content type (HTML / JSON / feed / PDF / text).
- **`replay/`** stores snapshots (local files or S3), keeps replay metadata, and computes field-level diffs between runs.
- **`policy/`** is the compliance layer: robots, PII, geo, and the append-only audit log.
- **`crawl/`** is the crawl machinery: the SQLite-backed queue, the async scheduler, dedup, sitemap discovery, the URL mapper, and batch helpers.
- **`agent/`** is the MCP server and its tool schemas.

Top-level modules round it out: `api.py` (public `scrape` / `extract` / `crawl` / `crawl_stream`), `results.py` (typed results), `watch.py` (change tracking), `security.py` (the SSRF guard), `_db.py` (pooled SQLite connections), `logging.py`, `web.py` (the local UI), and `cli.py` (the Typer CLI).

## Adding things

### A new LLM adapter

LLM adapters live in `scrapo/extract/llm_adapters/`. Implement the `LLMAdapter` base (the `Protocol` in `base.py`): set `provider` and `model_id`, and implement `async def extract_json(self, prompt, *, schema=None, max_tokens=2048) -> LLMResponse`. Return an `LLMResponse` with the parsed `json_payload`, the token counts, and the computed `cost_usd`.

- **Add pricing.** Follow the existing adapters (`anthropic_adapter.py`, `openai_adapter.py`, `gemini_adapter.py`): keep a `_PRICING_USD_PER_MTOK` table keyed by model id and compute `cost_usd` from input/output tokens. Cost accounting is what makes `Budget(...)` enforceable, so a new adapter without pricing is incomplete.
- **Import the SDK lazily** inside the adapter (optional-dependency rule) and wire the provider name into `get_default()` in `base.py`.

### A new proxy adapter

Proxy adapters live in `scrapo/access/adapters/`. Implement the `ProxyAdapter` protocol from `adapters/base.py`: set a `name` and implement `async def get_proxy(self, geo=None) -> ProxyConfig | None`, returning a `ProxyConfig(url=..., region=...)` (or `None` for a direct connection). Register it via `registry.register(...)` so it shows up in `scrapo adapters`. The built-in adapters (`brightdata`, `oxylabs`, `scrapfly`, `zyte`) are short and worth copying. Strip credentials before anything gets logged.

### Testing expectation

Every new adapter or feature ships with **offline** tests: stub HTTP with `respx` / `pytest-httpx`, monkeypatch the tiers or the SDK client, and use fakes or the `mock` LLM adapter. No test may touch the network or a paid LLM.

## Commit and PR style

- Conventional-ish subjects: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- Small, focused PRs: one logical change per PR where practical. Explain the "why" in the body.
- Update `CHANGELOG.md` under `[Unreleased]` for any user-visible change.
- Add yourself to [CONTRIBUTORS.md](CONTRIBUTORS.md) in the same PR.

## Reporting security issues

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
