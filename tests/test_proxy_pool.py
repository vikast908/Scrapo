import asyncio

from scrapo.access.adapters.base import ProxyConfig
from scrapo.access.proxy_pool import ProxyPool, _is_hard_block, _redact, report_outcome
from scrapo.types import FetchResult, Tier


def _result(status: int = 200, *, blocked: bool = False, reason: str | None = None) -> FetchResult:
    return FetchResult(
        url="https://e.com/x",
        final_url="https://e.com/x",
        status=status,
        html="<html>ok</html>",
        headers={},
        tier_used=Tier.HTTP,
        blocked=blocked,
        block_reason=reason,
    )


class _StubAdapter:
    name = "stub"

    async def get_proxy(self, geo: str | None = None) -> ProxyConfig | None:
        return ProxyConfig(url="http://upstream:9", region=geo)


# --- rotation --------------------------------------------------------------

async def test_round_robins_over_urls():
    pool = ProxyPool(["http://a:1", "http://b:2", "http://c:3"])
    seen = [(await pool.get_proxy()).url for _ in range(4)]
    assert seen == ["http://a:1", "http://b:2", "http://c:3", "http://a:1"]
    cfg = await pool.get_proxy(geo="US")
    assert cfg is not None
    assert cfg.url == "http://b:2"
    assert cfg.key == "http://b:2"
    assert cfg.region == "US"


async def test_post_init_trims_and_drops_blanks():
    pool = ProxyPool([" http://a:1 ", "", "  ", "http://b:2"])
    assert pool.urls == ["http://a:1", "http://b:2"]


# --- health / cooldown -----------------------------------------------------

async def test_soft_failures_park_after_max_failures():
    pool = ProxyPool(["http://a:1", "http://b:2"], max_failures=2, cooldown_seconds=60)
    await pool.report("http://a:1", ok=False)
    await pool.report("http://a:1", ok=False)  # second soft failure -> parked
    assert {(await pool.get_proxy()).url for _ in range(4)} == {"http://b:2"}


async def test_hard_block_parks_immediately():
    pool = ProxyPool(["http://a:1", "http://b:2"], cooldown_seconds=60)
    await pool.report("http://a:1", ok=False, hard=True)
    assert {(await pool.get_proxy()).url for _ in range(4)} == {"http://b:2"}


async def test_success_resets_failure_streak():
    pool = ProxyPool(["http://a:1"], max_failures=3, cooldown_seconds=60)
    await pool.report("http://a:1", ok=False)
    await pool.report("http://a:1", ok=False)
    assert (await pool.get_proxy()).url == "http://a:1"  # 2 < 3, still in rotation
    await pool.report("http://a:1", ok=True)  # reset
    await pool.report("http://a:1", ok=False)
    await pool.report("http://a:1", ok=False)
    assert (await pool.get_proxy()).url == "http://a:1"  # streak restarted


async def test_cooldown_expires():
    pool = ProxyPool(["http://a:1"], cooldown_seconds=0.05)
    await pool.report("http://a:1", ok=False, hard=True)
    assert await pool.get_proxy() is None  # parked, no upstream -> direct connection
    await asyncio.sleep(0.07)
    assert (await pool.get_proxy()).url == "http://a:1"  # back in rotation


async def test_report_unknown_key_is_noop():
    pool = ProxyPool(["http://a:1"])
    await pool.report("http://not-in-pool", ok=False, hard=True)  # must not raise
    assert (await pool.get_proxy()).url == "http://a:1"


# --- upstream fallback -----------------------------------------------------

async def test_falls_back_to_upstream_when_all_parked():
    pool = ProxyPool(["http://a:1"], cooldown_seconds=60, upstream=_StubAdapter())
    await pool.report("http://a:1", ok=False, hard=True)
    cfg = await pool.get_proxy(geo="DE")
    assert cfg is not None
    assert cfg.url == "http://upstream:9"
    assert cfg.key == "upstream:stub"
    await pool.report("upstream:stub", ok=False, hard=True)  # untracked key -> no-op


# --- stats / redaction -----------------------------------------------------

def test_stats_shape_and_credential_redaction():
    pool = ProxyPool(["http://user:pw@a:1", "http://b:2"])
    rows = pool.stats()
    assert len(rows) == 2
    assert rows[0]["proxy"] == "http://a:1"  # creds stripped
    assert set(rows[0]) == {"proxy", "successes", "failures", "cooling_down", "cooldown_remaining_s"}
    assert rows[0]["cooling_down"] is False


def test_redact():
    assert _redact("http://user:pw@host:8080") == "http://host:8080"
    assert _redact("http://host:8080") == "http://host:8080"
    assert _redact("host:8080") == "host:8080"


# --- from_env --------------------------------------------------------------

def test_from_env(monkeypatch):
    monkeypatch.setenv("SCRAPO_PROXY_URLS", " http://a:1 , http://b:2 ,")
    monkeypatch.setenv("SCRAPO_PROXY_COOLDOWN", "30")
    pool = ProxyPool.from_env()
    assert pool is not None
    assert pool.urls == ["http://a:1", "http://b:2"]
    assert pool.cooldown_seconds == 30.0
    monkeypatch.setenv("SCRAPO_PROXY_URLS", "")
    assert ProxyPool.from_env() is None


# --- report_outcome / _is_hard_block --------------------------------------

def test_is_hard_block_classification():
    assert _is_hard_block(_result(200)) is False
    assert _is_hard_block(_result(403, blocked=True, reason="http-403")) is True
    assert _is_hard_block(_result(429, blocked=True, reason="http-429")) is True
    assert _is_hard_block(_result(200, blocked=True, reason="cloudflare")) is True
    assert _is_hard_block(_result(503, blocked=True, reason="http-503")) is False
    assert _is_hard_block(_result(0, blocked=True, reason="network:ConnectError")) is False
    assert _is_hard_block(_result(200, blocked=True, reason="empty-body")) is False


async def test_report_outcome_feeds_pool():
    pool = ProxyPool(["http://a:1", "http://b:2"], cooldown_seconds=60)
    pcfg = ProxyConfig(url="http://a:1", key="http://a:1")
    await report_outcome(pool, pcfg, _result(403, blocked=True, reason="http-403"))
    assert {(await pool.get_proxy()).url for _ in range(4)} == {"http://b:2"}  # a parked


async def test_report_outcome_noop_without_key_or_report_method():
    pool = ProxyPool(["http://a:1"], cooldown_seconds=60)
    # ProxyConfig from a plain adapter has no key -> ignored, a stays usable
    await report_outcome(pool, ProxyConfig(url="http://a:1"), _result(403, blocked=True, reason="http-403"))
    assert (await pool.get_proxy()).url == "http://a:1"
    # an adapter without .report() -> harmless
    await report_outcome(object(), ProxyConfig(url="http://a:1", key="k"), _result(200))


async def test_report_outcome_marks_success():
    pool = ProxyPool(["http://a:1"], max_failures=2, cooldown_seconds=60)
    pcfg = ProxyConfig(url="http://a:1", key="http://a:1")
    await report_outcome(pool, pcfg, _result(503, blocked=True, reason="http-503"))  # soft, 1
    await report_outcome(pool, pcfg, _result(200))  # success -> reset
    await report_outcome(pool, pcfg, _result(503, blocked=True, reason="http-503"))  # soft, 1 again
    assert (await pool.get_proxy()).url == "http://a:1"  # not parked
