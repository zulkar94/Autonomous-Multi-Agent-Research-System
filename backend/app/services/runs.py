"""Run lifecycle: scheduling, persistence, cancellation, graceful shutdown.

Runs execute as supervised asyncio tasks inside the API process, bounded by a
semaphore. For multi-node deployments, replace `RunManager.submit` with an
enqueue to Redis/RabbitMQ and run this module inside a dedicated worker.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from ..agents.base import AgentContext, Budget
from ..agents.orchestrator import Cancelled, Orchestrator
from ..config import get_settings
from ..db import session_scope
from ..logging_setup import get_logger
from ..models import Claim, ClaimStatus, Run, RunEvent, RunStatus, Source
from ..providers.llm import get_llm_provider
from ..providers.search import get_search_provider
from .events import Event, bus

logger = get_logger(__name__)


class RunManager:
    """Owns in-flight research runs for the lifetime of the process."""

    def __init__(self) -> None:
        settings = get_settings()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_runs)
        self._seq: dict[str, int] = {}

    # ---------------------------------------------------------------- public

    async def submit(self, run_id: str, query: str, depth: int) -> None:
        task = asyncio.create_task(self._execute(run_id, query, depth), name=f"run:{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t: self._tasks.pop(run_id, None))

    def cancel(self, run_id: str) -> bool:
        if run_id not in self._tasks:
            return False
        self._cancelled.add(run_id)
        return True

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    @property
    def active_count(self) -> int:
        return len(self._tasks)

    async def shutdown(self, timeout: float = 10.0) -> None:  # noqa: ASYNC109
        """Signal cancellation and wait briefly so runs can persist their state."""
        for run_id in list(self._tasks):
            self._cancelled.add(run_id)
        if self._tasks:
            await asyncio.wait(list(self._tasks.values()), timeout=timeout)
            for task in list(self._tasks.values()):
                task.cancel()

    # --------------------------------------------------------------- private

    async def _emit(self, run_id: str, agent: str, phase: str, message: str, **kwargs: Any) -> None:
        seq = self._seq.get(run_id, 0) + 1
        self._seq[run_id] = seq
        payload = kwargs.get("payload")
        event = Event(
            run_id=run_id,
            seq=seq,
            agent=agent,
            phase=phase,
            message=message,
            level=kwargs.get("level", "warn" if phase == "warn" else "info"),
            payload=payload,
        )
        async with session_scope() as session:
            session.add(
                RunEvent(
                    run_id=run_id,
                    seq=seq,
                    agent=agent,
                    phase=phase,
                    level=event.level,
                    message=message[:4000],
                    payload_json=json.dumps(payload)[:20000] if payload else None,
                )
            )
        await bus.publish(event)

    async def _execute(self, run_id: str, query: str, depth: int) -> None:
        settings = get_settings()
        llm = search = None
        async with self._semaphore:
            try:
                await self._set_status(run_id, RunStatus.running)
                llm, search = get_llm_provider(), get_search_provider()

                async def emit(agent: str, phase: str, message: str, payload=None, level="info"):
                    await self._emit(run_id, agent, phase, message, payload=payload, level=level)

                ctx = AgentContext(
                    run_id=run_id,
                    query=query,
                    depth=depth,
                    settings=settings,
                    llm=llm,
                    search=search,
                    emit=emit,
                    budget=Budget(max_seconds=float(settings.run_timeout_seconds)),
                    cancelled=lambda: run_id in self._cancelled,
                )
                outcome = await asyncio.wait_for(
                    Orchestrator(ctx).run(), timeout=settings.run_timeout_seconds
                )
                await self._persist(run_id, outcome)

            except Cancelled:
                await self._set_status(run_id, RunStatus.cancelled)
                await self._emit(run_id, "orchestrator", "cancelled", "Run cancelled by user")
            except TimeoutError:
                await self._fail(run_id, "run exceeded its time budget")
            except asyncio.CancelledError:
                await self._set_status(run_id, RunStatus.cancelled)
                raise
            except Exception as exc:
                logger.exception("run_failed run_id=%s", run_id)
                await self._fail(run_id, f"{type(exc).__name__}: {exc}"[:500])
            finally:
                self._cancelled.discard(run_id)
                self._seq.pop(run_id, None)
                for provider in (llm, search):
                    if provider is not None:
                        await provider.aclose()
                await bus.close(run_id)

    async def _set_status(self, run_id: str, status: RunStatus) -> None:
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            run.status = status
            if status in (RunStatus.cancelled, RunStatus.failed, RunStatus.completed):
                run.finished_at = datetime.now(UTC)

    async def _fail(self, run_id: str, error: str) -> None:
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is not None:
                run.status = RunStatus.failed
                run.error = error
                run.finished_at = datetime.now(UTC)
        await self._emit(run_id, "orchestrator", "error", f"Run failed: {error}", level="error")

    async def _persist(self, run_id: str, outcome) -> None:  # type: ignore[no-untyped-def]
        async with session_scope() as session:
            run = await session.get(Run, run_id)
            if run is None:
                return
            for source in outcome.sources:
                session.add(
                    Source(
                        run_id=run_id,
                        ref=source.ref,
                        url=source.url[:2048],
                        domain=source.domain[:255],
                        title=source.title[:512],
                        snippet=source.snippet[:4000],
                        content_hash=source.content_hash,
                        credibility=source.credibility,
                    )
                )
            for claim in outcome.claims:
                session.add(
                    Claim(
                        run_id=run_id,
                        subquestion=claim.subquestion[:2000],
                        text=claim.text[:4000],
                        status=ClaimStatus(claim.status),
                        support=claim.support,
                        source_refs=claim.refs_csv[:512],
                        rebuttals="\n".join(claim.rebuttals)[:8000] or None,
                        rounds=claim.rounds,
                    )
                )
            finished = datetime.now(UTC)
            run.status = RunStatus.completed
            run.report_markdown = outcome.report_markdown
            run.confidence = outcome.confidence
            run.citation_coverage = outcome.citation_coverage
            run.tokens_used = outcome.tokens_used
            run.finished_at = finished
            created = run.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=UTC)
            run.duration_ms = int((finished - created).total_seconds() * 1000)


manager = RunManager()


async def recover_orphaned_runs() -> None:
    """Mark runs left `running` by a crashed process as failed at startup."""
    async with session_scope() as session:
        rows = await session.execute(select(Run).where(Run.status == RunStatus.running))
        for run in rows.scalars():
            run.status = RunStatus.failed
            run.error = "interrupted by process restart"
            run.finished_at = datetime.now(UTC)
