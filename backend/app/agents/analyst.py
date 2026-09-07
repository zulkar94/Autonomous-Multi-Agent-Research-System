"""Analyst: turns source text into discrete, ref-bound claims."""

from __future__ import annotations

import asyncio

from ..security.sanitize import wrap_untrusted
from .base import Agent
from .prompts import ANALYST
from .types import ClaimRecord, SourceRecord


class AnalystAgent(Agent):
    name = "analyst"
    role = "extraction"

    async def run(self, subquestions: list[str], sources: list[SourceRecord]) -> list[ClaimRecord]:
        await self.say("start", f"Extracting claims for {len(subquestions)} sub-questions")
        if not sources:
            await self.say("warn", "No sources available; nothing to extract")
            return []

        results = await asyncio.gather(
            *(self._extract(sq, sources) for sq in subquestions), return_exceptions=True
        )
        claims: list[ClaimRecord] = []
        for item in results:
            if isinstance(item, BaseException):
                await self.say("warn", f"Extraction failed: {type(item).__name__}")
                continue
            claims.extend(item)

        claims = self._dedupe(claims)
        await self.say("claims", f"Extracted {len(claims)} candidate claims")
        return claims

    async def _extract(self, subquestion: str, sources: list[SourceRecord]) -> list[ClaimRecord]:
        evidence = "\n\n".join(
            wrap_untrusted(f"{s.ref} | {s.domain} | {s.title}", s.snippet) for s in sources
        )
        valid_refs = {s.ref for s in sources}
        prompt = (
            f"RESEARCH QUESTION: {self.ctx.query}\nSUB-QUESTION: {subquestion}\n\n"
            f"SOURCES (refs {', '.join(sorted(valid_refs))}):\n{evidence}\n\n"
            "Extract up to 4 claims that answer the sub-question."
        )
        result = await self.think(ANALYST, prompt, tag="analyze", temperature=0.2)
        data = result.json(default={}) or {}

        claims: list[ClaimRecord] = []
        for item in data.get("claims", [])[:4]:
            text = str(item.get("text", "")).strip()
            refs = [r for r in item.get("source_refs", []) if r in valid_refs]
            if not text or not refs:
                continue  # uncited claims are dropped, never repaired
            claims.append(
                ClaimRecord(
                    subquestion=subquestion,
                    text=text,
                    source_refs=refs,
                    confidence=float(item.get("confidence", 0.5) or 0.5),
                )
            )
        return claims

    @staticmethod
    def _dedupe(claims: list[ClaimRecord]) -> list[ClaimRecord]:
        seen: set[str] = set()
        unique = []
        for claim in claims:
            key = " ".join(claim.text.lower().split())[:160]
            if key in seen:
                continue
            seen.add(key)
            unique.append(claim)
        return unique
