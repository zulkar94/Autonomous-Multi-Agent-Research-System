"""Liveness, readiness, metrics and admin audit endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import desc, func, select

from .. import __version__
from ..models import AuditLog, Run, RunStatus, User
from ..schemas import HealthOut
from ..services.runs import manager
from .deps import AdminUser, SessionDep, SettingsDep

router = APIRouter(tags=["system"])


@router.get("/healthz", response_model=HealthOut)
async def healthz(settings: SettingsDep) -> HealthOut:
    return HealthOut(status="ok", version=__version__, environment=settings.app_env)


@router.get("/readyz", response_model=HealthOut)
async def readyz(session: SessionDep, settings: SettingsDep) -> HealthOut:
    await session.execute(select(1))
    return HealthOut(status="ready", version=__version__, environment=settings.app_env)


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: SessionDep) -> PlainTextResponse:
    """Prometheus text exposition. Scrape from the internal network only."""
    users = await session.scalar(select(func.count()).select_from(User)) or 0
    lines = [
        "# HELP ars_users_total Registered users",
        "# TYPE ars_users_total gauge",
        f"ars_users_total {users}",
        "# HELP ars_runs_total Research runs by status",
        "# TYPE ars_runs_total gauge",
    ]
    for status_value in RunStatus:
        count = (
            await session.scalar(
                select(func.count()).select_from(Run).where(Run.status == status_value)
            )
            or 0
        )
        lines.append(f'ars_runs_total{{status="{status_value.value}"}} {count}')
    lines += [
        "# HELP ars_runs_active In-flight runs in this process",
        "# TYPE ars_runs_active gauge",
        f"ars_runs_active {manager.active_count}",
    ]
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")


@router.get("/admin/audit")
async def audit_log(
    session: SessionDep,
    _: AdminUser,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[dict]:
    rows = await session.scalars(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit))
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "action": row.action,
            "resource": row.resource,
            "ip": row.ip,
            "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
