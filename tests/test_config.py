from scrapo.config import Config


def test_from_env_uses_string_default_user_agent(monkeypatch, tmp_path):
    monkeypatch.delenv("SCRAPO_USER_AGENT", raising=False)
    monkeypatch.delenv("SCRAPO_RESPECT_ROBOTS", raising=False)
    monkeypatch.setenv("SCRAPO_DATA_DIR", str(tmp_path / "scrapo"))

    cfg = Config.from_env()

    assert isinstance(cfg.user_agent, str)
    assert cfg.user_agent.startswith("scrapo/")
    assert cfg.respect_robots is False


def test_from_env_can_enable_robots_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPO_DATA_DIR", str(tmp_path / "scrapo"))
    monkeypatch.setenv("SCRAPO_RESPECT_ROBOTS", "1")

    cfg = Config.from_env()

    assert cfg.respect_robots is True


def test_security_and_retry_defaults(monkeypatch, tmp_path):
    for var in ("SCRAPO_ALLOW_PRIVATE_HOSTS", "SCRAPO_REDACT_SNAPSHOTS", "SCRAPO_HTTP_RETRIES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SCRAPO_DATA_DIR", str(tmp_path / "scrapo"))

    cfg = Config.from_env()

    assert cfg.allow_private_hosts is False
    assert cfg.redact_snapshots is False
    assert cfg.http_retries == 2


def test_from_env_overrides_security_and_retry(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPO_DATA_DIR", str(tmp_path / "scrapo"))
    monkeypatch.setenv("SCRAPO_ALLOW_PRIVATE_HOSTS", "1")
    monkeypatch.setenv("SCRAPO_REDACT_SNAPSHOTS", "1")
    monkeypatch.setenv("SCRAPO_HTTP_RETRIES", "5")

    cfg = Config.from_env()

    assert cfg.allow_private_hosts is True
    assert cfg.redact_snapshots is True
    assert cfg.http_retries == 5


def test_proxy_pool_defaults_and_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCRAPO_DATA_DIR", str(tmp_path / "scrapo"))
    monkeypatch.delenv("SCRAPO_PROXY_URLS", raising=False)
    monkeypatch.delenv("SCRAPO_PROXY_COOLDOWN", raising=False)

    assert Config().proxy_urls == []
    assert Config().proxy_cooldown_seconds == 120.0

    monkeypatch.setenv("SCRAPO_PROXY_URLS", "http://a:1, http://b:2 ,")
    monkeypatch.setenv("SCRAPO_PROXY_COOLDOWN", "45")
    cfg = Config.from_env()
    assert cfg.proxy_urls == ["http://a:1", "http://b:2"]
    assert cfg.proxy_cooldown_seconds == 45.0
