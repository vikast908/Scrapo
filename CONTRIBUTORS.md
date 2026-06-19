# Contributors

Scrapo exists because people take the time to file bugs, sketch features, and send patches. Thank you to everyone who has helped, in code or otherwise.

## Maintainers

| Name | GitHub | Role |
|---|---|---|
| Scrapo author | [@vikast908](https://github.com/vikast908) | Repo owner, maintainer |

## Contributors

This project is currently maintained by its author. Contributions are welcome: open a PR and add yourself here.

When your PR lands, add a row to the table below.

| Name | GitHub handle | Area |
|---|---|---|
| _Your name_ | [@your-handle](https://github.com/your-handle) | e.g. extract, access, docs |

## How contributions are recognized

Every merged contribution earns a row in the table above. We credit by name and GitHub handle, alongside the area you worked on (access, extract, shape, replay, policy, crawl, agent, docs, or tests). Significant or sustained work may also be acknowledged in the release notes in [CHANGELOG.md](CHANGELOG.md).

New here? Set up with `pip install -e ".[dev]"` (add `playwright install chromium` for the browser tiers), then make sure the quality gates pass before opening a PR: `pytest -q` (the suite is fully offline — no test may hit the network or a paid LLM), `ruff check .`, and `mypy scrapo/` (strict). See the [Tests](README.md#tests) and [Configuration](README.md#configuration) sections of the README for more.
