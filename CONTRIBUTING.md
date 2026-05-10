# Contributing to Scrapo

Thanks for your interest. Bug reports, feature requests, and pull requests are all welcome.

## Dev setup

```bash
git clone https://github.com/vikast908/Scrapo
cd Scrapo
python -m venv .venv
. .venv/bin/activate          # on Windows: .venv\Scripts\activate
pip install -e ".[dev]"
playwright install chromium   # only needed for the browser tiers
```

## Before you push

The CI runs all three of these on Python 3.11 to 3.13; run them locally first:

```bash
ruff check .
mypy scrapo/
pytest -q
```

The test suite is fully offline by design: no test may hit the network or a paid LLM. Use `respx` / `pytest-httpx` to stub HTTP, monkeypatch the tiers, or use the `mock` LLM adapter. If you add a feature, add a test.

## House rules

- **Optional dependencies stay optional.** Anything under `[project.optional-dependencies]` (Playwright, the LLM SDKs, `mcp`) must be imported lazily inside the function that uses it, so `pip install scrapo` works with no extras. Return a `blocked` `FetchResult` (or raise a clear `ImportError`) when an extra is missing.
- **Type everything.** `mypy --strict` must pass with no new `# type: ignore`. Add one only with a specific error code and a short comment.
- **Keep `scrape` / `extract` / `crawl` signatures stable.** They are the public surface. New behavior goes behind a new keyword argument or a `Config` field with a sensible default.
- **No new network calls at import time.** (This is why `tldextract` was dropped.)
- **Touch the docs.** New config goes in the README config table; user-visible changes go in `CHANGELOG.md` under `[Unreleased]`.
- **Markdown style:** no em-dashes in docs. Use a colon, a comma, parentheses, or rewrite the sentence.

## Commit and PR style

- Conventional-ish subjects: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
- One logical change per PR where practical. Explain the "why" in the body.

## Reporting security issues

Do not open a public issue for a vulnerability. See [SECURITY.md](SECURITY.md).
