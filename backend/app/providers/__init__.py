"""Pluggable external providers: LLM completions, web search, page fetching."""

from .llm import LLMProvider, LLMResult, get_llm_provider
from .search import SearchProvider, SearchResult, get_search_provider

__all__ = [
    "LLMProvider",
    "LLMResult",
    "SearchProvider",
    "SearchResult",
    "get_llm_provider",
    "get_search_provider",
]
