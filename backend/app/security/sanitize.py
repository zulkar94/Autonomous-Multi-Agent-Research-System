"""Prompt-injection hardening for untrusted web content.

Retrieved text is data, never instruction. Content is stripped of markup,
truncated, screened for imperative override patterns, and wrapped in delimited
blocks that the system prompt explicitly declares untrusted.
"""

from __future__ import annotations

import html
import re

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{3,}")

INJECTION_PATTERNS = [
    re.compile(r"ignore (all |any |the )?(previous|prior|above) (instructions|prompts?)", re.I),
    re.compile(r"disregard (your|all|the) (rules|instructions|system prompt)", re.I),
    re.compile(r"you are now (a|an|the)\b", re.I),
    re.compile(r"(system|developer)\s*(prompt|message)\s*:", re.I),
    re.compile(r"</?(system|assistant|human)>", re.I),
    re.compile(r"reveal (your|the) (system )?(prompt|instructions|api key)", re.I),
    re.compile(r"\bexfiltrate\b|\bsend (the )?(secrets?|credentials?|api keys?)\b", re.I),
]

FENCE = "-----UNTRUSTED-WEB-CONTENT-----"


def strip_markup(text: str) -> str:
    text = _TAG.sub(" ", text)
    text = _ANY_TAG.sub(" ", text)
    text = html.unescape(text)
    text = _WS.sub(" ", text)
    return _NEWLINES.sub("\n\n", text).strip()


def detect_injection(text: str) -> list[str]:
    """Return the injection patterns matched by `text` (empty list when clean)."""
    return [p.pattern for p in INJECTION_PATTERNS if p.search(text)]


def sanitize_content(text: str, max_chars: int = 6000) -> tuple[str, list[str]]:
    """Return (safe_text, detected_injection_patterns)."""
    cleaned = strip_markup(text)
    findings = detect_injection(cleaned)
    for pattern in INJECTION_PATTERNS:
        cleaned = pattern.sub("[filtered]", cleaned)
    cleaned = cleaned.replace(FENCE, "[filtered]")
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars] + " …[truncated]"
    return cleaned, findings


def wrap_untrusted(label: str, text: str) -> str:
    """Delimit untrusted content so the model can never mistake it for orders."""
    safe, _ = sanitize_content(text)
    return f"{FENCE}\n[{label}]\n{safe}\n{FENCE}"
