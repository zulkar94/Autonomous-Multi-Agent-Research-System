"""Shared FastAPI dependencies: authentication, authorization, quotas."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db import get_session
from ..logging_setup import user_id_var
from ..models import Role, User
from ..security.ratelimit import limiter
from ..security.tokens import TokenError, decode_token

bearer = HTTPBearer(auto_error=False)

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _unauthorized(detail: str = "not authenticated") -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def current_user(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)] = None,
) -> User:
    if credentials is None or not credentials.credentials:
        raise _unauthorized()
    try:
        payload = decode_token(credentials.credentials, expected_kind="access")
    except TokenError as exc:
        raise _unauthorized(str(exc)) from exc

    user = await session.get(User, payload["sub"])
    if user is None or not user.is_active:
        raise _unauthorized("account is inactive")
    user_id_var.set(user.id)
    return user


CurrentUser = Annotated[User, Depends(current_user)]


async def require_admin(user: CurrentUser) -> User:
    if user.role != Role.admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin role required")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def enforce_run_quota(user: CurrentUser, settings: SettingsDep) -> None:
    """Per-user quota on expensive research runs, independent of the global limit."""
    if not settings.rate_limit_enabled:
        return
    decision = await limiter.check(
        f"runs:{user.id}", settings.rate_limit_run_creation, settings.rate_limit_run_window_seconds
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="run quota exceeded",
            headers={"Retry-After": str(decision.retry_after)},
        )


def client_ip(request: Request) -> str:
    """Direct peer address. Trust `X-Forwarded-For` only behind a known proxy."""
    return request.client.host if request.client else "unknown"
