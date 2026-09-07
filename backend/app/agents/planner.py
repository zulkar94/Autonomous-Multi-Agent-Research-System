"""Planner: decomposes the question into verifiable sub-questions."""

from __future__ import annotations

from ..logging_setup import get_logger
from .base import Agent
from .prompts import PLANNER
from .types import Plan

logger = get_logger(__name__)


class PlannerAgent(Agent):
    name = "planner"
    role = "decomposition"

    async def run(self) -> Plan:
        await self.say("start", "Decomposing the research question")
        limit = min(self.ctx.settings.max_subquestions, 2 + self.ctx.depth * 2)
        prompt = (
            f"RESEARCH QUESTION: {self.ctx.query}\n"
            f"Produce at most {limit} sub-questions and at most {limit} search queries."
        )
        result = await self.think(PLANNER, prompt, tag="plan", temperature=0.3)
        data = result.json(default={}) or {}

        subquestions = [str(q).strip() for q in data.get("subquestions", []) if str(q).strip()]
        queries = [str(q).strip() for q in data.get("search_queries", []) if str(q).strip()]
        if not subquestions:
            subquestions = [self.ctx.query]
        if not queries:
            queries = subquestions[:]

        plan = Plan(subquestions=subquestions[:limit], search_queries=queries[:limit])
        await self.say(
            "plan",
            f"Planned {len(plan.subquestions)} sub-questions",
            subquestions=plan.subquestions,
        )
        return plan
