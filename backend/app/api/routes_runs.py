"""Research run endpoints, including the SSE trace stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse, StreamingResponse
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from ..logging_setup import get_logger
from ..models import AuditLog, Run, RunEvent, RunStatus, User
from ..schemas import RunCreate, RunDetail, RunSummary, StreamTicket
from ..security.tokens import TokenError, create_token
from ..services.events import bus
from ..services.runs import manager
from .deps import CurrentUser, SessionDep, SettingsDep, client_ip, enforce_run_quota
from .routes_auth import verify_stream_ticket

logger = get_logger(__name__)
router = APIRouter(prefix="/research", tags=["research"])

HEARTBEAT_SECONDS = 15.0


async def _owned_run(session, run_id: str, user: User, *, eager: bool = False) -> Run:  # type: ignore[no-untyped-def]
    """Fetch a run scoped to its owner. Missing and forbidden both return 404."""
    stmt = select(Run).where(Run.id == run_id, Run.user_id == user.id)
    if eager:
        stmt = stmt.options(selectinload(Run.sources), selectinload(Run.claims))
    run = await session.scalar(stmt)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")
    return run


@router.post(
    "/runs",
    response_model=RunSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(enforce_run_quota)],
)
async def create_run(
    payload: RunCreate, request: Request, session: SessionDep, user: CurrentUser
) -> Run:
    run = Run(user_id=user.id, query=payload.query, depth=payload.depth, status=RunStatus.queued)
    session.add(run)
    session.add(
        AuditLog(user_id=user.id, action="run_create", resource=run.id, ip=client_ip(request))
    )
    await session.flush()
    run_id = run.id
    await session.commit()  # the worker reads this row from its own session
    await manager.submit(run_id, payload.query, payload.depth)
    logger.info("run_submitted run_id=%s depth=%d", run_id, payload.depth)
    return run


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    session: SessionDep,
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> list[Run]:
    rows = await session.scalars(
        select(Run)
        .where(Run.user_id == user.id)
        .order_by(desc(Run.created_at))
        .limit(limit)
        .offset(offset)
    )
    return list(rows)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: str, session: SessionDep, user: CurrentUser) -> Run:
    return await _owned_run(session, run_id, user, eager=True)


@router.get("/runs/{run_id}/report", response_class=PlainTextResponse)
async def get_report(run_id: str, session: SessionDep, user: CurrentUser) -> PlainTextResponse:
    run = await _owned_run(session, run_id, user)
    if not run.report_markdown:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="report not ready")
    return PlainTextResponse(
        run.report_markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="report-{run_id[:8]}.md"'},
    )


@router.post("/runs/{run_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_run(run_id: str, session: SessionDep, user: CurrentUser) -> dict[str, str]:
    run = await _owned_run(session, run_id, user)
    if run.status not in (RunStatus.queued, RunStatus.running):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="run is not active")
    manager.cancel(run_id)
    return {"status": "cancelling"}


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_run(run_id: str, session: SessionDep, user: CurrentUser) -> Response:
    run = await _owned_run(session, run_id, user)
    manager.cancel(run_id)
    await session.delete(run)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/runs/{run_id}/stream-ticket", response_model=StreamTicket)
async def stream_ticket(
    run_id: str, session: SessionDep, user: CurrentUser, settings: SettingsDep
) -> StreamTicket:
    """Short-lived, run-scoped ticket, because EventSource cannot send headers."""
    await _owned_run(session, run_id, user)
    ticket = create_token(user.id, "stream", extra={"run_id": run_id})
    return StreamTicket(ticket=ticket, expires_in=settings.stream_ticket_ttl_seconds)


@router.get("/runs/{run_id}/events")
async def stream_events(
    run_id: str,
    request: Request,
    session: SessionDep,
    ticket: Annotated[str, Query(min_length=10, max_length=4096)],
) -> StreamingResponse:
    """Server-sent event stream: replay of persisted trace, then live updates."""
    try:
        scoped_run_id = verify_stream_ticket(ticket)
    except TokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    if scoped_run_id != run_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="ticket scope mismatch")

    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="run not found")

    queue = await bus.subscribe(run_id)
    history = list(
        await session.scalars(
            select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.seq)
        )
    )
    replayed = {event.seq for event in history}
    terminal = run.status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled)

    async def generate() -> AsyncIterator[str]:
        try:
            for event in history:
                yield _sse(
                    {
                        "seq": event.seq,
                        "agent": event.agent,
                        "phase": event.phase,
                        "level": event.level,
                        "message": event.message,
                        "payload": json.loads(event.payload_json) if event.payload_json else None,
                    }
                )
            if terminal:
                yield _sse({"phase": "closed", "status": run.status.value}, event="end")
                return

            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                if item is None:
                    yield _sse({"phase": "closed"}, event="end")
                    break
                if item.seq in replayed:
                    continue
                yield _sse(item.to_dict())
        finally:
            await bus.unsubscribe(run_id, queue)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(data: dict, event: str = "message") -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
