"""LLM abstraction with an Anthropic implementation and a deterministic mock.

The mock keeps the whole pipeline runnable, testable and CI-friendly with no
API key and no network access — the default for `LLM_PROVIDER=mock`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import get_settings
from ..logging_setup import get_logger

logger = get_logger(__name__)

_JSON_BLOCK = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


class LLMError(RuntimeError):
    """Provider failure after retries are exhausted."""


@dataclass(slots=True)
class LLMResult:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def json(self, default: Any = None) -> Any:
        """Best-effort structured parse; tolerates fenced or prefixed output."""
        text = self.text.strip()
        text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = _JSON_BLOCK.search(text)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
        logger.warning("llm_json_parse_failed len=%d", len(text))
        return default


class LLMProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        tag: str = "generic",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Return a completion for a single-turn system+user exchange."""

    async def aclose(self) -> None:
        return None


class AnthropicProvider(LLMProvider):
    """Messages API client with bounded retries and exponential backoff + jitter."""

    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        s = get_settings()
        self._api_key = api_key or s.anthropic_api_key
        if not self._api_key:
            raise LLMError("ANTHROPIC_API_KEY is required for the anthropic provider")
        self._model = model or s.anthropic_model
        self._client = httpx.AsyncClient(
            base_url=s.anthropic_base_url,
            timeout=httpx.Timeout(s.llm_timeout_seconds),
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
        )

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        tag: str = "generic",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        s = get_settings()
        body = {
            "model": self._model,
            "max_tokens": max_tokens or s.llm_max_tokens,
            "temperature": s.llm_temperature if temperature is None else temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }
        last_error: Exception | None = None
        for attempt in range(s.llm_max_retries):
            try:
                response = await self._client.post("/v1/messages", json=body)
                if response.status_code in (429, 500, 502, 503, 529):
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                data = response.json()
                text = "".join(
                    block.get("text", "")
                    for block in data.get("content", [])
                    if block.get("type") == "text"
                )
                usage = data.get("usage", {})
                return LLMResult(
                    text=text,
                    input_tokens=usage.get("input_tokens", 0),
                    output_tokens=usage.get("output_tokens", 0),
                    model=data.get("model", self._model),
                    meta={"tag": tag},
                )
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                backoff = min(2**attempt, 8) + random.uniform(0, 0.5)  # noqa: S311  # nosec B311
                logger.warning(
                    "llm_retry attempt=%d tag=%s backoff=%.2f", attempt + 1, tag, backoff
                )
                await asyncio.sleep(backoff)
        raise LLMError(f"anthropic request failed after retries: {last_error}")

    async def aclose(self) -> None:
        await self._client.aclose()


class MockLLMProvider(LLMProvider):
    """Deterministic, offline stand-in. Output is seeded by prompt hash."""

    name = "mock"

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        tag: str = "generic",
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        await asyncio.sleep(0)  # keep the coroutine cooperative
        seed = int(hashlib.sha256(f"{tag}:{prompt}".encode()).hexdigest()[:8], 16)
        rng = random.Random(seed)  # noqa: S311  # nosec B311
        text = self._render(tag, prompt, rng)
        return LLMResult(
            text=text,
            input_tokens=len(prompt) // 4,
            output_tokens=len(text) // 4,
            model="mock-1",
            meta={"tag": tag},
        )

    def _render(self, tag: str, prompt: str, rng: random.Random) -> str:
        topic = self._topic(prompt)
        if tag == "plan":
            angles = [
                f"What is the current state of {topic}?",
                f"What evidence supports the main claims about {topic}?",
                f"What are the strongest counter-arguments regarding {topic}?",
                f"Which measurable outcomes are reported for {topic}?",
                f"What risks or limitations apply to {topic}?",
            ]
            return json.dumps(
                {
                    "subquestions": angles[: rng.randint(3, 5)],
                    "search_queries": [f"{topic} evidence", f"{topic} analysis", f"{topic} risks"],
                }
            )
        if tag == "analyze":
            refs = list(dict.fromkeys(re.findall(r"\[(S\d+)\]", prompt))) or ["S1"]
            aspect = self._subquestion(prompt)
            claims = []
            for i, ref in enumerate(refs[:3]):
                # Each sub-question yields distinct wording so the analyst's
                # de-duplication pass does not collapse the whole fixture set.
                claims.append(
                    {
                        "text": (
                            f"On \u201c{aspect}\u201d, {ref} reports a measurable effect on "
                            f"{topic} of roughly {12 + i * 7}% within the sampled cohort."
                        ),
                        "source_refs": [ref] if i else refs[:2],
                        "confidence": round(rng.uniform(0.55, 0.92), 2),
                    }
                )
            return json.dumps({"claims": claims})
        if tag == "verify":
            return json.dumps(
                {
                    "verdict": rng.choice(["supported", "supported", "uncertain"]),
                    "support": round(rng.uniform(0.5, 0.95), 2),
                    "reason": "Cited passages align with the claim without contradicting evidence.",
                }
            )
        if tag == "critique":
            return json.dumps(
                {
                    "challenges": [
                        {
                            "severity": rng.choice(["low", "medium"]),
                            "issue": "Sample scope may not generalise beyond the cited context.",
                        }
                    ]
                }
            )
        if tag == "rebut":
            return json.dumps(
                {
                    "response": "Scope narrowed to the cited context; claim wording qualified.",
                    "revised_text": None,
                    "concede": rng.random() < 0.3,
                }
            )
        if tag == "synthesize":
            return (
                f"## Summary\n\nEvidence on {topic} converges on a moderate, "
                "measurable effect [S1].\n\n## Findings\n\n- Primary sources agree on direction "
                "of effect [S1].\n- Magnitude estimates vary across contexts [S2].\n\n"
                "## Limitations\n\nSource diversity is limited; treat magnitudes as indicative."
            )
        return json.dumps({"result": f"mock response for {tag}"})

    @staticmethod
    def _subquestion(prompt: str) -> str:
        match = re.search(r"SUB-QUESTION:\s*(.+)", prompt)
        return (match.group(1) if match else "the question").strip()[:90]

    @staticmethod
    def _topic(prompt: str) -> str:
        match = re.search(r"(?:RESEARCH QUESTION|QUESTION|TOPIC):\s*(.+)", prompt)
        raw = match.group(1) if match else prompt[:80]
        return raw.strip().strip(".?").lower()[:80] or "the topic"


def get_llm_provider() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "anthropic":
        return AnthropicProvider()
    return MockLLMProvider()
