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
