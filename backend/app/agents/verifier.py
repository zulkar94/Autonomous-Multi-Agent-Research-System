"""Verifier: adjudicates each claim against its cited evidence."""

from __future__ import annotations

import asyncio

from ..security.sanitize import wrap_untrusted
from .base import Agent
from .prompts import VERIFIER
from .types import ClaimRecord, SourceRecord

VALID_VERDICTS = {"supported", "uncertain", "refuted"}


class VerifierAgent(Agent):
    name = "verifier"
    role = "verification"

    async def run(
        self, claims: list[ClaimRecord], sources: list[SourceRecord]
    ) -> list[ClaimRecord]:
        await self.say("start", f"Verifying {len(claims)} claims against cited evidence")
        by_ref = {s.ref: s for s in sources}
        semaphore = asyncio.Semaphore(4)

        async def verify(claim: ClaimRecord) -> ClaimRecord:
            async with semaphore:
                return await self._verify_one(claim, by_ref)

        results = await asyncio.gather(*(verify(c) for c in claims), return_exceptions=True)
        verified = [r for r in results if isinstance(r, ClaimRecord)]

        supported = sum(1 for c in verified if c.status == "supported")
        await self.say(
            "verdicts",
            f"{supported}/{len(verified)} claims supported",
            breakdown={
                status: sum(1 for c in verified if c.status == status)
                for status in sorted(VALID_VERDICTS)
            },
        )
        return verified

    async def _verify_one(self, claim: ClaimRecord, by_ref: dict[str, SourceRecord]) -> ClaimRecord:
        cited = [by_ref[r] for r in claim.source_refs if r in by_ref]
        if not cited:
            claim.status, claim.support = "refuted", 0.0
            return claim

        evidence = "\n\n".join(wrap_untrusted(f"{s.ref} | {s.domain}", s.snippet) for s in cited)
        prompt = (
            f"CLAIM: {claim.text}\nCITED REFS: {claim.refs_csv}\n\nEVIDENCE:\n{evidence}\n\n"
            "Judge whether the evidence supports the claim as written."
        )
        try:
            result = await self.think(VERIFIER, prompt, tag="verify", temperature=0.1)
        except Exception:
            claim.status, claim.support = "uncertain", 0.3
            return claim

        data = result.json(default={}) or {}
        verdict = str(data.get("verdict", "uncertain")).lower()
        claim.status = verdict if verdict in VALID_VERDICTS else "uncertain"
        support = float(data.get("support", 0.0) or 0.0)

        # Corroboration and source quality modulate the raw model score.
        independence = min(1.0, 0.6 + 0.2 * len({s.domain for s in cited}))
        quality = sum(s.credibility for s in cited) / len(cited)
        claim.support = round(min(1.0, max(0.0, support * independence * (0.6 + 0.4 * quality))), 3)
        if claim.status == "supported" and claim.support < self.ctx.settings.min_claim_support:
            claim.status = "uncertain"
        return claim
