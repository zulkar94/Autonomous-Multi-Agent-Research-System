"""Pipeline coordinator: plan → search → analyze → verify → debate → synthesize."""

from __future__ import annotations

import time

from ..logging_setup import get_logger
from .analyst import AnalystAgent
from .base import AgentContext
from .debate import DebateModerator
from .planner import PlannerAgent
from .searcher import SearcherAgent
from .synthesizer import SynthesizerAgent
from .types import ResearchOutcome
from .verifier import VerifierAgent

logger = get_logger(__name__)


class Cancelled(RuntimeError):  # noqa: N818 - reads naturally at call sites
    """Raised when a run is cancelled between stages."""


class Orchestrator:
    """Sequences the agent stages and enforces cancellation checkpoints."""

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx
        self.planner = PlannerAgent(ctx)
        self.searcher = SearcherAgent(ctx)
        self.analyst = AnalystAgent(ctx)
        self.verifier = VerifierAgent(ctx)
        self.moderator = DebateModerator(ctx, self.verifier)
        self.synthesizer = SynthesizerAgent(ctx)

    def _checkpoint(self) -> None:
        if self.ctx.cancelled():
            raise Cancelled("run cancelled")

    async def run(self) -> ResearchOutcome:
        started = time.monotonic()
        await self.ctx.emit(
            agent="orchestrator", phase="start", message=f"Run started: {self.ctx.query[:120]}"
        )

        plan = await self.planner.run()
        self._checkpoint()

        sources = await self.searcher.run(plan)
        self._checkpoint()

        claims = await self.analyst.run(plan.subquestions, sources)
        self._checkpoint()

        claims = await self.verifier.run(claims, sources)
        self._checkpoint()

        claims = await self.moderator.run(claims, sources)
        self._checkpoint()

        report, confidence, coverage = await self.synthesizer.run(claims, sources)

        elapsed = (time.monotonic() - started) * 1000
        await self.ctx.emit(
            agent="orchestrator",
            phase="done",
            message=f"Run finished in {elapsed:.0f} ms",
            payload={"confidence": confidence, "coverage": coverage},
        )
        return ResearchOutcome(
            report_markdown=report,
            claims=claims,
            sources=sources,
            confidence=confidence,
            citation_coverage=coverage,
            tokens_used=self.ctx.budget.tokens_used,
        )
