"""Agent pipeline behaviour with offline providers."""

from __future__ import annotations

import asyncio

from app.agents.base import AgentContext, Budget
from app.agents.orchestrator import Cancelled, Orchestrator
from app.agents.types import ClaimRecord, SourceRecord
from app.config import get_settings
from app.providers.llm import MockLLMProvider
from app.providers.search import MockSearchProvider, credibility_of
from app.services.citations import (
    citation_coverage,
    enforce_valid_refs,
    extract_refs,
    uncited_sentences,
)


def make_context(**overrides) -> AgentContext:
    events: list[dict] = []

    async def emit(agent: str, phase: str, message: str, payload=None, level="info") -> None:
        events.append({"agent": agent, "phase": phase, "message": message})

    kwargs = {
        "run_id": "test-run",
        "query": "Does structured code review reduce production defects?",
        "depth": 2,
        "settings": get_settings(),
        "llm": MockLLMProvider(),
        "search": MockSearchProvider(),
        "emit": emit,
        "budget": Budget(max_seconds=60),
    }
    kwargs.update(overrides)
    ctx = AgentContext(**kwargs)
    ctx.emit.events = events  # type: ignore[attr-defined]
    return ctx


async def test_full_pipeline_produces_cited_report() -> None:
    ctx = make_context()
    outcome = await Orchestrator(ctx).run()

    assert outcome.sources, "pipeline must retrieve sources"
    assert outcome.claims, "pipeline must produce claims"
    assert "## References" in outcome.report_markdown
    assert outcome.citation_coverage > 0
    assert 0.0 <= outcome.confidence <= 1.0
    assert outcome.tokens_used > 0

    valid_refs = {s.ref for s in outcome.sources}
    for ref in extract_refs(outcome.report_markdown):
        assert ref in valid_refs, "report must never cite an unknown source"


async def test_every_claim_carries_a_valid_citation() -> None:
    ctx = make_context()
    outcome = await Orchestrator(ctx).run()
    valid_refs = {s.ref for s in outcome.sources}
    for claim in outcome.claims:
        assert claim.source_refs
        assert set(claim.source_refs) <= valid_refs


async def test_debate_records_rounds_and_scores() -> None:
    ctx = make_context(depth=3)
    outcome = await Orchestrator(ctx).run()
    assert any(c.rounds > 0 or c.rebuttals for c in outcome.claims)
    assert all(0.0 <= c.support <= 1.0 for c in outcome.claims)


async def test_cancellation_is_honoured() -> None:
    ctx = make_context(cancelled=lambda: True)
    try:
        await Orchestrator(ctx).run()
    except Cancelled:
        return
    raise AssertionError("orchestrator ignored the cancellation flag")


async def test_budget_ceiling_stops_llm_calls() -> None:
    ctx = make_context()
    ctx.budget.max_calls = 1
    ctx.budget.llm_calls = 1
    assert ctx.budget.exhausted()


async def test_search_results_are_deduplicated_and_scored() -> None:
    provider = MockSearchProvider()
    results = await provider.search("code review defects", limit=5)
    assert len({r.url for r in results}) == len(results)
    assert credibility_of("https://nih.gov/study") > credibility_of("http://medium.com/post")


def test_hallucinated_refs_are_stripped() -> None:
    text = "Effect is positive [S1]. Another statement [S99]."
    cleaned, removed = enforce_valid_refs(text, {"S1"})
    assert removed == 1
    assert "[S99]" not in cleaned
    assert "[S1]" in cleaned


def test_citation_coverage_and_gap_detection() -> None:
    text = (
        "Structured review reduces defect escape rates in the sampled teams [S1]. "
        "A second sentence asserts something substantive without any citation at all."
    )
    assert 0.0 < citation_coverage(text, {"S1"}) < 1.0
    assert len(uncited_sentences(text, {"S1"})) == 1


async def test_mock_llm_is_deterministic() -> None:
    llm = MockLLMProvider()
    first, second = await asyncio.gather(
        llm.complete("sys", "RESEARCH QUESTION: x", tag="plan"),
        llm.complete("sys", "RESEARCH QUESTION: x", tag="plan"),
    )
    assert first.text == second.text


def test_claim_record_refs_csv() -> None:
    claim = ClaimRecord(subquestion="q", text="t", source_refs=["S1", "S3"])
    assert claim.refs_csv == "S1,S3"
    source = SourceRecord("S1", "https://x.org/a", "x.org", "T", "snippet", 0.7)
    assert source.credibility == 0.7
