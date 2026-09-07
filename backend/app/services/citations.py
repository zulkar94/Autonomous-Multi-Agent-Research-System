"""Citation integrity: ref validation, coverage scoring, reference rendering."""

from __future__ import annotations

import re

REF_PATTERN = re.compile(r"\[(S\d{1,3})\]")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def extract_refs(text: str) -> list[str]:
    return REF_PATTERN.findall(text)


def enforce_valid_refs(text: str, valid_refs: set[str]) -> tuple[str, int]:
    """Delete citation markers the synthesizer invented. Returns (text, removed)."""
    removed = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal removed
        if match.group(1) in valid_refs:
            return match.group(0)
        removed += 1
        return ""

    return REF_PATTERN.sub(replace, text), removed


def _is_factual(sentence: str) -> bool:
    stripped = sentence.strip()
    if len(stripped) < 40:
        return False
    return not stripped.startswith(("#", ">", "|", "```"))


def citation_coverage(text: str, valid_refs: set[str]) -> float:
    """Share of substantive sentences carrying at least one valid citation."""
    body = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith(("#", "```"))
    )
    sentences = [s for s in SENTENCE_SPLIT.split(body) if _is_factual(s)]
    if not sentences:
        return 0.0
    cited = sum(1 for s in sentences if any(r in valid_refs for r in extract_refs(s)))
    return round(cited / len(sentences), 3)


def uncited_sentences(text: str, valid_refs: set[str]) -> list[str]:
    """Substantive sentences with no valid citation — surfaced for review."""
    sentences = [s for s in SENTENCE_SPLIT.split(text) if _is_factual(s)]
    return [s.strip() for s in sentences if not any(r in valid_refs for r in extract_refs(s))]


def references_block(sources: list) -> str:
    if not sources:
        return ""
    lines = ["## References", ""]
    for source in sources:
        lines.append(
            f"- **[{source.ref}]** [{source.title}]({source.url}) — {source.domain} "
            f"(credibility {source.credibility:.2f})"
        )
    return "\n".join(lines)
