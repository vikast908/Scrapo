"""DeepSeek adapter wiring (no network — construction & selection only)."""

from __future__ import annotations

import pytest

pytest.importorskip("openai")  # the DeepSeek adapter rides the OpenAI client

from scrapo.extract.llm_adapters.base import get_default
from scrapo.extract.llm_adapters.deepseek_adapter import DeepSeekAdapter


def test_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.delenv("SCRAPO_DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)
    a = DeepSeekAdapter()
    assert a.provider == "deepseek"
    assert a.model_id == "deepseek-v4-flash"
    assert str(a._client.base_url).rstrip("/") == "https://api.deepseek.com"


def test_model_override_via_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SCRAPO_DEEPSEEK_MODEL", "deepseek-reasoner")
    assert DeepSeekAdapter().model_id == "deepseek-reasoner"


def test_explicit_model_id_wins(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("SCRAPO_DEEPSEEK_MODEL", "deepseek-reasoner")
    assert DeepSeekAdapter(model_id="some-new-model").model_id == "some-new-model"


def test_get_default_selects_deepseek(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    adapter = get_default("deepseek")
    assert adapter.provider == "deepseek"
    assert isinstance(adapter, DeepSeekAdapter)


def test_cost_uses_pricing_table(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    a = DeepSeekAdapter(model_id="deepseek-v4-flash")
    # 1M input + 1M output at (0.14, 0.28) USD/Mtok
    assert a._cost(1_000_000, 1_000_000) == pytest.approx(0.42)


def test_unknown_model_falls_back_to_nonzero_cost(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    a = DeepSeekAdapter(model_id="deepseek-zzz-unreleased")  # not in the table
    assert a._cost(1_000_000, 0) > 0
