"""Searcher: retrieves, de-duplicates, egress-checks and scores sources."""

from __future__ import annotations

import asyncio

from ..providers.search import credibility_of
from .base import Agent
from .types import Plan, SourceRecord


class SearcherAgent(Agent):
    name = "searcher"
    role = "retrieval"

    async def run(self, plan: Plan) -> list[SourceRecord]:
        await self.say("start", f"Searching {len(plan.search_queries)} queries")
        per_query = self.ctx.settings.max_sources_per_question

        tasks = [self._search_one(q, per_query) for q in plan.search_queries]
        batches = await asyncio.gather(*tasks, return_exceptions=True)

        sources: list[SourceRecord] = []
        seen_urls: set[str] = set()
        seen_domains: dict[str, int] = {}

        for batch in batches:
            if isinstance(batch, BaseException):
                await self.say("warn", f"Search failed: {type(batch).__name__}")
                continue
            for result in batch:
                if result.url in seen_urls:
                    continue
                domain = result.domain
                # Cap per-domain dominance so one publisher cannot carry a report.
                if seen_domains.get(domain, 0) >= 3:
                    continue
                seen_urls.add(result.url)
                seen_domains[domain] = seen_domains.get(domain, 0) + 1
                sources.append(
                    SourceRecord(
                        ref=f"S{len(sources) + 1}",
                        url=result.url,
                        domain=domain,
                        title=result.title or domain,
                        snippet=result.snippet,
                        credibility=credibility_of(result.url, base=result.score),
                    )
                )

        await self.say(
            "sources",
            f"Collected {len(sources)} unique sources across {len(seen_domains)} domains",
            sources=[{"ref": s.ref, "url": s.url, "credibility": s.credibility} for s in sources],
        )
        return sources

    async def _search_one(self, query: str, limit: int) -> list:
        return await self.ctx.search.search(query, limit=limit)
