"""LLM adapters — uniform interface across Anthropic, OpenAI, Gemini, mocks."""

from scrapo.extract.llm_adapters.base import LLMAdapter, LLMResponse, get_default

__all__ = ["LLMAdapter", "LLMResponse", "get_default"]
