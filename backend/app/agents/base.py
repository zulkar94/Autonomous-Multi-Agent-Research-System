"""Shared agent scaffolding: context, telemetry, safe LLM invocation."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..logging_setup import get_logger
from ..providers.llm import LLMError, LLMProvider, LLMResult
from ..providers.search import SearchProvider

logger = get_logger(__name__)

EmitFn = Callable[..., Awaitable[None]]


@dataclass(slots=True)
class Budget:
    """Hard ceilings so a runaway debate cannot exhaust tokens or wall-clock."""

    tokens_used: int = 0
    llm_calls: int = 0
    max_tokens: int = 400_000
    max_calls: int = 120
    started: float = field(default_factory=time.monotonic)
    max_seconds: float = 900.0

    def charge(self, result: LLMResult) -> None:
        self.tokens_used += result.total_tokens
        self.llm_calls += 1

    def exhausted(self) -> bool:
        return (
            self.tokens_used >= self.max_tokens
            or self.llm_calls >= self.max_calls
            or time.monotonic() - self.started >= self.max_seconds
        )


@dataclass(slots=True)
class AgentContext:
    run_id: str
    query: str
    depth: int
    settings: Settings
    llm: LLMProvider
    search: SearchProvider
    emit: EmitFn
    budget: Budget
    cancelled: Callable[[], bool] = lambda: False


class Agent(ABC):
    """Base agent. Subclasses implement `run` and emit their own trace events."""

    name: str = "agent"
    role: str = "generic"

    def __init__(self, ctx: AgentContext) -> None:
        self.ctx = ctx

    async def say(self, phase: str, message: str, **payload: Any) -> None:
        await self.ctx.emit(agent=self.name, phase=phase, message=message, payload=payload or None)

    async def think(
        self,
        system: str,
        prompt: str,
        *,
        tag: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResult:
        """Invoke the LLM with budget accounting and failure isolation."""
        if self.ctx.budget.exhausted():
            raise LLMError("agent budget exhausted")
        try:
            result = await self.ctx.llm.complete(
                system, prompt, tag=tag, max_tokens=max_tokens, temperature=temperature
            )
        except LLMError:
            raise
        except Exception as exc:  # provider bug, transport surprise
            raise LLMError(f"{self.name} llm call failed: {exc}") from exc
        self.ctx.budget.charge(result)
        return result

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """Execute this agent's stage of the pipeline."""
