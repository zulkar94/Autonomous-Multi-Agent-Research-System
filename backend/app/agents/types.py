"""Value objects passed between agents (decoupled from the ORM layer)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class SourceRecord:
    ref: str
    url: str
    domain: str
    title: str
    snippet: str
    credibility: float
    content_hash: str | None = None
    injection_flagged: bool = False


@dataclass(slots=True)
class ClaimRecord:
    subquestion: str
    text: str
    source_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    support: float = 0.0
    status: str = "uncertain"
    rebuttals: list[str] = field(default_factory=list)
    rounds: int = 0

    @property
    def refs_csv(self) -> str:
        return ",".join(self.source_refs)


@dataclass(slots=True)
class Plan:
    subquestions: list[str]
    search_queries: list[str]


@dataclass(slots=True)
class ResearchOutcome:
    report_markdown: str
    claims: list[ClaimRecord]
    sources: list[SourceRecord]
    confidence: float
    citation_coverage: float
    tokens_used: int
