"""Global configuration — env-overridable, sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

from scrapo.types import Tier

_APP_NAME = "scrapo"
_DEFAULT_USER_AGENT = "scrapo/0.1 (+https://github.com/anthropics/scrapo)"


def _default_data_dir() -> Path:
    override = os.environ.get("SCRAPO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(_APP_NAME, appauthor=False))


@dataclass(slots=True)
class Config:
    data_dir: Path = field(default_factory=_default_data_dir)
    user_agent: str = _DEFAULT_USER_AGENT
    request_timeout: float = 30.0
    max_concurrency: int = 8
    default_max_tier: Tier = Tier.BROWSER
    respect_robots: bool = False
    enable_pii_filter: bool = False
    redact_snapshots: bool = False
    main_content: bool = False  # strip boilerplate (nav/sidebar/footer) before markdown
    audit_enabled: bool = True
    snapshot_html: bool = True
    snapshot_backend: str = "local"  # "local" or "s3://bucket/prefix"
    browser_block_resources: bool = True  # drop images/fonts/media/css in the browser tier
    browser_capture_xhr: bool = True  # surface JSON XHR/fetch responses on FetchResult
    allow_private_hosts: bool = False
    http_retries: int = 2
    conditional_requests: bool = True  # re-scrapes send If-None-Match/If-Modified-Since and reuse the archive on 304
    proxy_adapter: str | None = None
    proxy_urls: list[str] = field(default_factory=list)  # static proxy pool, rotated with health checks
    proxy_cooldown_seconds: float = 120.0  # how long a parked proxy stays out of rotation
    agent_driver: str | None = None  # "llm" to use the built-in LLMAgentDriver at tier 4
    agent_action_cache: bool = True  # record/replay agent action sequences at tier 4
    llm_adapter: str | None = "anthropic"
    llm_model: str = "claude-opus-4-7"
    geo: str | None = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        if self.request_timeout <= 0:
            raise ValueError(f"request_timeout must be positive, got {self.request_timeout}")
        if self.max_concurrency < 1:
            raise ValueError(f"max_concurrency must be >= 1, got {self.max_concurrency}")
        if self.http_retries < 0:
            raise ValueError(f"http_retries must be >= 0, got {self.http_retries}")
        if self.proxy_cooldown_seconds < 0:
            raise ValueError(
                f"proxy_cooldown_seconds must be >= 0, got {self.proxy_cooldown_seconds}"
            )
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"cannot create scrapo data dir at {self.data_dir} "
                f"(set SCRAPO_DATA_DIR to a writable path): {exc}"
            ) from exc

    @property
    def replay_db(self) -> Path:
        return self.data_dir / "replay.sqlite"

    @property
    def selector_cache_db(self) -> Path:
        return self.data_dir / "selectors.sqlite"

    @property
    def crawl_queue_db(self) -> Path:
        return self.data_dir / "queue.sqlite"

    @property
    def action_cache_db(self) -> Path:
        return self.data_dir / "agent_actions.sqlite"

    @property
    def audit_log(self) -> Path:
        return self.data_dir / "audit.log"

    @property
    def snapshot_dir(self) -> Path:
        d = self.data_dir / "snapshots"
        d.mkdir(parents=True, exist_ok=True)
        return d

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            user_agent=os.environ.get("SCRAPO_USER_AGENT", _DEFAULT_USER_AGENT),
            request_timeout=_env_float("SCRAPO_TIMEOUT", 30.0),
            max_concurrency=_env_int("SCRAPO_CONCURRENCY", 8),
            respect_robots=os.environ.get("SCRAPO_RESPECT_ROBOTS", "0") == "1",
            enable_pii_filter=os.environ.get("SCRAPO_PII_FILTER", "0") == "1",
            redact_snapshots=os.environ.get("SCRAPO_REDACT_SNAPSHOTS", "0") == "1",
            main_content=os.environ.get("SCRAPO_MAIN_CONTENT", "0") == "1",
            snapshot_backend=os.environ.get("SCRAPO_SNAPSHOT_BACKEND", "local"),
            browser_block_resources=os.environ.get("SCRAPO_BROWSER_BLOCK_RESOURCES", "1") == "1",
            browser_capture_xhr=os.environ.get("SCRAPO_BROWSER_CAPTURE_XHR", "1") == "1",
            allow_private_hosts=os.environ.get("SCRAPO_ALLOW_PRIVATE_HOSTS", "0") == "1",
            http_retries=_env_int("SCRAPO_HTTP_RETRIES", 2),
            conditional_requests=os.environ.get("SCRAPO_CONDITIONAL_REQUESTS", "1") == "1",
            proxy_adapter=os.environ.get("SCRAPO_PROXY_ADAPTER") or None,
            proxy_urls=[u.strip() for u in os.environ.get("SCRAPO_PROXY_URLS", "").split(",") if u.strip()],
            proxy_cooldown_seconds=_env_float("SCRAPO_PROXY_COOLDOWN", 120.0),
            agent_driver=os.environ.get("SCRAPO_AGENT_DRIVER") or None,
            agent_action_cache=os.environ.get("SCRAPO_AGENT_ACTION_CACHE", "1") == "1",
            llm_adapter=os.environ.get("SCRAPO_LLM_ADAPTER", "anthropic"),
            llm_model=os.environ.get("SCRAPO_LLM_MODEL", "claude-opus-4-7"),
            geo=os.environ.get("SCRAPO_GEO") or None,
        )


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


_active: Config | None = None


def get_config() -> Config:
    global _active
    if _active is None:
        _active = Config.from_env()
    return _active


def set_config(config: Config) -> None:
    global _active
    _active = config
