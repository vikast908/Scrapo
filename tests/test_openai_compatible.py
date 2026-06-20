"""Generic OpenAI-compatible adapter + model-agnostic provider selection.

No network: only construction and factory routing/auto-detection are exercised.
"""

from __future__ import annotations

import pytest

pytest.importorskip("openai")

from scrapo.extract.llm_adapters.base import get_default
from scrapo.extract.llm_adapters.mock_adapter import MockAdapter
from scrapo.extract.llm_adapters.openai_compatible import OpenAICompatibleAdapter

_ENV = [
    "SCRAPO_LLM_ADAPTER", "SCRAPO_LLM_BASE_URL", "SCRAPO_LLM_API_KEY", "SCRAPO_LLM_MODEL",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY",
    "GEMINI_API_KEY",
]


@pytest.fixture
def clean_env(monkeypatch):
    for k in _ENV:
        monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_generic_adapter_defaults(clean_env):
    a = OpenAICompatibleAdapter(base_url="http://localhost:1234/v1")
    assert a.provider == "openai-compatible"
    assert a.model_id == "gpt-4o-mini"  # class default
    assert "localhost:1234" in str(a._client.base_url)


def test_generic_adapter_model_and_provider_override(clean_env):
    a = OpenAICompatibleAdapter(model_id="m", base_url="http://x/v1", provider="groq")
    assert a.provider == "groq"
    assert a.model_id == "m"


def test_autodetect_mock_when_no_keys(clean_env):
    assert isinstance(get_default(), MockAdapter)


def test_autodetect_openai_from_key(clean_env):
    clean_env.setenv("OPENAI_API_KEY", "sk-x")
    assert get_default().provider == "openai"


def test_autodetect_order_deepseek_before_openrouter(clean_env):
    clean_env.setenv("DEEPSEEK_API_KEY", "sk-d")
    clean_env.setenv("OPENROUTER_API_KEY", "sk-o")
    assert get_default().provider == "deepseek"  # deepseek precedes openrouter in the order


def test_autodetect_via_base_url(clean_env):
    clean_env.setenv("SCRAPO_LLM_BASE_URL", "http://localhost:8000/v1")
    assert get_default().provider == "openai-compatible"


def test_explicit_openrouter(clean_env):
    clean_env.setenv("OPENROUTER_API_KEY", "sk-or")
    a = get_default("openrouter")
    assert a.provider == "openrouter"
    assert "openrouter.ai" in str(a._client.base_url)


def test_explicit_ollama_needs_no_key(clean_env):
    a = get_default("ollama")
    assert a.provider == "ollama"
    assert "11434" in str(a._client.base_url)
    assert a.model_id == "llama3.1"


def test_explicit_custom_endpoint(clean_env):
    clean_env.setenv("SCRAPO_LLM_BASE_URL", "http://localhost:8000/v1")
    clean_env.setenv("SCRAPO_LLM_MODEL", "my-local-model")
    a = get_default("openai-compatible")
    assert a.provider == "openai-compatible"
    assert a.model_id == "my-local-model"
    assert "localhost:8000" in str(a._client.base_url)


def test_unknown_provider_falls_back_to_mock(clean_env):
    clean_env.setenv("SCRAPO_LLM_ADAPTER", "totally-made-up")
    assert isinstance(get_default(), MockAdapter)
