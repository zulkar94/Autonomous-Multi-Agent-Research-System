"""Synthesizer: writes the cited report and computes confidence metrics."""

from __future__ import annotations

from ..services.citations import citation_coverage, enforce_valid_refs, references_block
from .base import Agent
from .prompts import SYNTHESIZER
from .types import ClaimRecord, SourceRecord


class SynthesizerAgent(Agent):
    name = "synthesizer"
    role = "synthesis"

    async def run(
        self, claims: list[ClaimRecord], sources: list[SourceRecord]
    ) -> tuple[str, float, float]:
        usable = [c for c in claims if c.status in ("supported", "uncertain")]
        await self.say("start", f"Synthesizing report from {len(usable)} claims")

        if not usable:
            report = (
                f"# Research report\n\n**Question:** {self.ctx.query}\n\n"
                "No claim survived verification. Re-run with a narrower question or "
                "different sources.\n"
            )
            return report, 0.0, 0.0

        claim_block = "\n".join(
            f"- [{c.status} {c.support:.2f}] {c.text} {''.join(f'[{r}]' for r in c.source_refs)}"
            for c in usable
        )
        disagreements = (
            "\n".join(
                f"- {c.text[:200]} :: {'; '.join(c.rebuttals[:2])}" for c in usable if c.rebuttals
            )
            or "- none recorded"
        )
        source_block = "\n".join(
            f"[{s.ref}] {s.title} — {s.domain} (credibility {s.credibility:.2f})" for s in sources
        )
        prompt = (
            f"RESEARCH QUESTION: {self.ctx.query}\n\nVERIFIED CLAIMS:\n{claim_block}\n\n"
            f"DEBATE RECORD:\n{disagreements}\n\nAVAILABLE SOURCES:\n{source_block}\n\n"
            "Write the report. Cite only the refs listed above."
        )
        result = await self.think(SYNTHESIZER, prompt, tag="synthesize", max_tokens=2500)

        valid_refs = {s.ref for s in sources}
        body, stripped = enforce_valid_refs(result.text, valid_refs)
        if stripped:
            await self.say("warn", f"Removed {stripped} hallucinated citation refs")

        coverage = citation_coverage(body, valid_refs)
        confidence = self._confidence(usable, coverage)
        report = (
            f"# Research report\n\n**Question:** {self.ctx.query}\n\n"
            f"**Confidence:** {confidence:.0%} · **Citation coverage:** {coverage:.0%} · "
            f"**Sources:** {len(sources)} · **Claims:** {len(usable)}\n\n---\n\n"
            f"{body.strip()}\n\n{references_block(sources)}\n"
        )
        await self.say("report", f"Report complete: confidence {confidence:.0%}")
        return report, confidence, coverage

    @staticmethod
    def _confidence(claims: list[ClaimRecord], coverage: float) -> float:
        """Weighted blend of verification strength, supported ratio and coverage."""
        if not claims:
            return 0.0
        mean_support = sum(c.support for c in claims) / len(claims)
        supported_ratio = sum(1 for c in claims if c.status == "supported") / len(claims)
        volume = min(1.0, len(claims) / 6)
        score = 0.45 * mean_support + 0.25 * supported_ratio + 0.2 * coverage + 0.1 * volume
        return round(min(1.0, max(0.0, score)), 3)
