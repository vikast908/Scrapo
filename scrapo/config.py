"""Global configuration — env-overridable, sensible defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_data_dir

from scrapo.types import Tier

_APP_NAME = "scrapo"


def _default_data_dir() -> Path:
    override = os.environ.get("SCRAPO_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(user_data_dir(_APP_NAME, appauthor=False))


@dataclass(slots=True)
class Config:
    data_dir: Path = field(default_factory=_default_data_dir)
    user_agent: str = "scrapo/0.1 (+https://github.com/anthropics/scrapo)"
    request_timeout: float = 30.0
    max_concurrency: int = 8
    default_max_tier: Tier = Tier.BROWSER
    respect_robots: bool = True
    enable_pii_filter: bool = False
    audit_enabled: bool = True
    snapshot_html: bool = True
    proxy_adapter: str | None = None
    llm_adapter: str | None = "anthropic"
    llm_model: str = "claude-opus-4-7"
    geo: str | None = None

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir).expanduser()
        self.data_dir.mkdir(parents=True, exist_ok=True)

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
            user_agent=os.environ.get("SCRAPO_USER_AGENT", cls.user_agent),
            request_timeout=float(os.environ.get("SCRAPO_TIMEOUT", "30")),
            max_concurrency=int(os.environ.get("SCRAPO_CONCURRENCY", "8")),
            respect_robots=os.environ.get("SCRAPO_RESPECT_ROBOTS", "1") == "1",
            enable_pii_filter=os.environ.get("SCRAPO_PII_FILTER", "0") == "1",
            proxy_adapter=os.environ.get("SCRAPO_PROXY_ADAPTER") or None,
            llm_adapter=os.environ.get("SCRAPO_LLM_ADAPTER", "anthropic"),
            llm_model=os.environ.get("SCRAPO_LLM_MODEL", "claude-opus-4-7"),
            geo=os.environ.get("SCRAPO_GEO") or None,
        )


_active: Config | None = None


def get_config() -> Config:
    global _active
    if _active is None:
        _active = Config.from_env()
    return _active


def set_config(config: Config) -> None:
    global _active
    _active = config
