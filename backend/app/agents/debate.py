"""Adversarial debate: Critic challenges, Analyst rebuts, Verifier re-scores.

Rounds stop early once no medium/high-severity challenge survives, so cheap
claims settle fast and contested ones get the budget.
"""

from __future__ import annotations

from ..security.sanitize import wrap_untrusted
from .base import Agent
from .prompts import CRITIC, REBUTTAL
from .types import ClaimRecord, SourceRecord
from .verifier import VerifierAgent

SEVERITY_WEIGHT = {"low": 0.02, "medium": 0.08, "high": 0.2}


class DebateModerator(Agent):
    name = "moderator"
    role = "debate"

    def __init__(self, ctx, verifier: VerifierAgent) -> None:  # type: ignore[no-untyped-def]
        super().__init__(ctx)
        self._verifier = verifier

    async def run(
        self, claims: list[ClaimRecord], sources: list[SourceRecord]
    ) -> list[ClaimRecord]:
        rounds = min(self.ctx.settings.max_debate_rounds, self.ctx.depth)
        by_ref = {s.ref: s for s in sources}
        contested = [c for c in claims if c.status != "refuted"]
        await self.say("start", f"Opening debate: {len(contested)} claims, up to {rounds} rounds")

        for round_no in range(1, rounds + 1):
            if self.ctx.cancelled() or self.ctx.budget.exhausted():
                await self.say("halt", f"Debate stopped early at round {round_no}")
                break

            unresolved = 0
            for claim in contested:
                challenges = await self._critique(claim, by_ref)
                if not challenges:
                    continue
                serious = [c for c in challenges if c.get("severity") in ("medium", "high")]
                if not serious:
                    continue
                unresolved += 1
                await self._rebut(claim, serious, by_ref, round_no)

            await self.say(
                "round", f"Round {round_no}: {unresolved} claims contested", round=round_no
            )
            if unresolved == 0:
                break

        rescored = await self._verifier.run(contested, sources)
        refuted = [c for c in claims if c.status == "refuted"]
        return rescored + refuted

    async def _critique(self, claim: ClaimRecord, by_ref: dict[str, SourceRecord]) -> list[dict]:
        cited = [by_ref[r] for r in claim.source_refs if r in by_ref]
        evidence = "\n\n".join(wrap_untrusted(s.ref, s.snippet) for s in cited)
        prompt = (
            f"CLAIM: {claim.text}\nSUPPORT SCORE: {claim.support}\n\nEVIDENCE:\n{evidence}\n\n"
            "List substantive challenges only."
        )
        try:
            result = await self.think(CRITIC, prompt, tag="critique", temperature=0.4)
        except Exception:
            return []
        data = result.json(default={}) or {}
        return [c for c in data.get("challenges", []) if isinstance(c, dict)][:3]

    async def _rebut(
        self,
        claim: ClaimRecord,
        challenges: list[dict],
        by_ref: dict[str, SourceRecord],
        round_no: int,
    ) -> None:
        cited = [by_ref[r] for r in claim.source_refs if r in by_ref]
        evidence = "\n\n".join(wrap_untrusted(s.ref, s.snippet) for s in cited)
        issues = "\n".join(f"- [{c.get('severity')}] {c.get('issue')}" for c in challenges)
        prompt = (
            f"CLAIM: {claim.text}\n\nCHALLENGES:\n{issues}\n\nEVIDENCE:\n{evidence}\n\n"
            "Concede or defend, narrowing the claim if warranted."
        )
        try:
            result = await self.think(REBUTTAL, prompt, tag="rebut", temperature=0.3)
        except Exception:
            return
        data = result.json(default={}) or {}

        claim.rounds = round_no
        response = str(data.get("response", "")).strip()
        if response:
            claim.rebuttals.append(f"R{round_no}: {response}")
        revised = data.get("revised_text")
        if isinstance(revised, str) and len(revised.strip()) > 15:
            claim.text = revised.strip()

        penalty = sum(SEVERITY_WEIGHT.get(str(c.get("severity")), 0.02) for c in challenges)
        if data.get("concede"):
            penalty += 0.15
            claim.status = "uncertain"
        claim.support = round(max(0.0, claim.support - penalty), 3)
        await self.say(
            "exchange",
            f"Round {round_no}: claim contested ({len(challenges)} issues)",
            claim=claim.text[:160],
            support=claim.support,
        )
