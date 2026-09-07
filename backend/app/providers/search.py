"""Web search abstraction (Tavily, Brave, deterministic mock) with SSRF filtering."""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from ..config import get_settings
from ..logging_setup import get_logger
from ..security.ssrf import is_safe_url

logger = get_logger(__name__)

# Coarse credibility priors by domain class. Tuned, not authoritative — the
# verifier agent still has to corroborate every claim.
TIER_1 = (".gov", ".edu", ".int", "who.int", "nature.com", "science.org", "nih.gov", "arxiv.org")
TIER_2 = ("reuters.com", "apnews.com", "bbc.co.uk", "ft.com", "economist.com", "ieee.org")
TIER_LOW = ("blogspot.", "medium.com", "substack.com", "reddit.com", "quora.com", "pinterest.")


@dataclass(slots=True)
class SearchResult:
    title: str
    url: str
    snippet: str
    score: float = 0.5

    @property
    def domain(self) -> str:
        return urlsplit(self.url).hostname or "unknown"


def credibility_of(url: str, base: float = 0.5) -> float:
    """Heuristic prior in [0, 1] combining domain class and transport security."""
    host = (urlsplit(url).hostname or "").lower()
    score = base
    if any(host.endswith(t) or t in host for t in TIER_1):
        score += 0.35
    elif any(t in host for t in TIER_2):
        score += 0.2
    if any(t in host for t in TIER_LOW):
        score -= 0.2
    if not url.startswith("https://"):
        score -= 0.1
    return round(min(1.0, max(0.05, score)), 3)


class SearchProvider(ABC):
    name = "base"

    @abstractmethod
    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        """Return ranked results for `query`, already filtered by egress policy."""

    async def aclose(self) -> None:
        return None

    @staticmethod
    def _filter(results: list[SearchResult]) -> list[SearchResult]:
        safe, seen = [], set()
        for result in results:
            if result.url in seen:
                continue
            if not is_safe_url(result.url):
                logger.warning("search_result_blocked domain=%s", result.domain)
                continue
            seen.add(result.url)
            safe.append(result)
        return safe


class TavilyProvider(SearchProvider):
    name = "tavily"

    def __init__(self) -> None:
        s = get_settings()
        if not s.tavily_api_key:
            raise ValueError("TAVILY_API_KEY is required for the tavily provider")
        self._key = s.tavily_api_key
        self._client = httpx.AsyncClient(timeout=s.search_timeout_seconds)

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        response = await self._client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": self._key,
                "query": query[:400],
                "max_results": limit,
                "search_depth": "advanced",
            },
        )
        response.raise_for_status()
        payload = response.json()
        return self._filter(
            [
                SearchResult(
                    title=item.get("title", "")[:500],
                    url=item.get("url", ""),
                    snippet=(item.get("content") or "")[:2000],
                    score=float(item.get("score", 0.5)),
                )
                for item in payload.get("results", [])
            ]
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class BraveProvider(SearchProvider):
    name = "brave"

    def __init__(self) -> None:
        s = get_settings()
        if not s.brave_api_key:
            raise ValueError("BRAVE_API_KEY is required for the brave provider")
        self._client = httpx.AsyncClient(
            timeout=s.search_timeout_seconds,
            headers={"X-Subscription-Token": s.brave_api_key, "Accept": "application/json"},
        )

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        response = await self._client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query[:400], "count": limit},
        )
        response.raise_for_status()
        payload = response.json()
        return self._filter(
            [
                SearchResult(
                    title=item.get("title", "")[:500],
                    url=item.get("url", ""),
                    snippet=(item.get("description") or "")[:2000],
                )
                for item in payload.get("web", {}).get("results", [])
            ]
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class MockSearchProvider(SearchProvider):
    """Offline fixture provider. Deterministic per query; no network, no keys."""

    name = "mock"
    DOMAINS = [
        ("ncbi.nlm.nih.gov", "Peer-reviewed analysis"),
        ("reuters.com", "News report"),
        ("arxiv.org", "Preprint study"),
        ("economist.com", "Sector briefing"),
        ("medium.com", "Practitioner essay"),
    ]

    async def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        digest = hashlib.sha256(query.encode()).hexdigest()
        results = []
        for i, (domain, kind) in enumerate(self.DOMAINS[:limit]):
            slug = digest[i * 6 : i * 6 + 6]
            results.append(
                SearchResult(
                    title=f"{kind}: {query[:70]}",
                    url=f"https://{domain}/research/{slug}",
                    snippet=(
                        f"{kind} examining {query[:120]}. Reported effects are consistent in "
                        f"direction across the sampled cohort, with magnitude varying by context."
                    ),
                    score=round(0.9 - i * 0.1, 2),
                )
            )
        return results  # fixtures bypass DNS on purpose


def get_search_provider() -> SearchProvider:
    s = get_settings()
    if s.search_provider == "tavily":
        return TavilyProvider()
    if s.search_provider == "brave":
        return BraveProvider()
    return MockSearchProvider()
