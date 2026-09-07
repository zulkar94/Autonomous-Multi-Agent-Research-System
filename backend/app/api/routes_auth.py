"""Authentication: registration, login, refresh rotation, logout, profile."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from ..config import get_settings
from ..logging_setup import get_logger
from ..models import AuditLog, RefreshToken, Role, User, utcnow
from ..schemas import LoginRequest, RefreshRequest, RegisterRequest, TokenPair, UserOut
from ..security.passwords import hash_password, needs_rehash, verify_password
from ..security.tokens import (
    TokenError,
    create_token,
    decode_token,
    generate_refresh_token,
    hash_refresh_token,
)
from .deps import CurrentUser, SessionDep, client_ip

logger = get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["auth"])

MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15

# Verified against a real hash so the unknown-user path costs the same as the
# known-user path (defence against timing-based account enumeration).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(24))


async def _audit(session, user_id, action, request, detail=None) -> None:  # type: ignore[no-untyped-def]
    session.add(
        AuditLog(
            user_id=user_id, action=action, ip=client_ip(request), detail=detail, resource="auth"
        )
    )


async def _issue_pair(session, user: User, family_id: str | None = None) -> TokenPair:  # type: ignore[no-untyped-def]
    settings = get_settings()
    raw, digest = generate_refresh_token()
    record = RefreshToken(
        user_id=user.id,
        token_hash=digest,
        family_id=family_id or digest[:36],
        expires_at=datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds),
    )
    session.add(record)
    access = create_token(user.id, "access", extra={"role": user.role.value})
    return TokenPair(
        access_token=access,
        refresh_token=raw,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, session: SessionDep) -> User:
    email = payload.email.lower()
    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        # Same status and shape as success would be ideal, but a duplicate email
        # is unavoidably observable; keep the message generic.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="registration failed")

    is_first = (await session.scalar(select(User.id).limit(1))) is None
    user = User(
        email=email,
        password_hash=hash_password(payload.password),
        role=Role.admin if is_first else Role.user,
    )
    session.add(user)
    await session.flush()
    await _audit(session, user.id, "register", request)
    logger.info("user_registered role=%s", user.role.value)
    return user


@router.post("/login", response_model=TokenPair)
async def login(payload: LoginRequest, request: Request, session: SessionDep) -> TokenPair:
    generic = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    user = await session.scalar(select(User).where(User.email == payload.email.lower()))

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise generic

    now = datetime.now(UTC)
    locked_until = user.locked_until
    if locked_until is not None:
        if locked_until.tzinfo is None:
            locked_until = locked_until.replace(tzinfo=UTC)
        if locked_until > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="account temporarily locked"
            )

    if not user.is_active or not verify_password(payload.password, user.password_hash):
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
            user.failed_logins = 0
            await _audit(session, user.id, "login_locked", request)
        else:
            await _audit(session, user.id, "login_failed", request)
        await session.commit()  # failure counters must survive the raised 401
        raise generic

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.failed_logins = 0
    user.locked_until = None
    await _audit(session, user.id, "login", request)
    return await _issue_pair(session, user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(payload: RefreshRequest, request: Request, session: SessionDep) -> TokenPair:
    digest = hash_refresh_token(payload.refresh_token)
    record = await session.scalar(select(RefreshToken).where(RefreshToken.token_hash == digest))
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token"
    )
    if record is None:
        raise invalid

    now = datetime.now(UTC)
    expires_at = record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if record.revoked_at is not None:
        # Replay of a rotated token: revoke the entire family.
        family = await session.scalars(
            select(RefreshToken).where(RefreshToken.family_id == record.family_id)
        )
        for token in family:
            token.revoked_at = token.revoked_at or now
        await _audit(session, record.user_id, "refresh_reuse_detected", request)
        await session.commit()  # revocation must survive the raised 401
        logger.warning("refresh_token_reuse family=%s", record.family_id)
        raise invalid
    if expires_at <= now:
        raise invalid

    user = await session.get(User, record.user_id)
    if user is None or not user.is_active:
        raise invalid

    record.revoked_at = now
    return await _issue_pair(session, user, family_id=record.family_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, request: Request, session: SessionDep) -> Response:
    record = await session.scalar(
        select(RefreshToken).where(
            RefreshToken.token_hash == hash_refresh_token(payload.refresh_token)
        )
    )
    if record is not None and record.revoked_at is None:
        record.revoked_at = utcnow()
        await _audit(session, record.user_id, "logout", request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


def verify_stream_ticket(ticket: str) -> str:
    """Return the run id a stream ticket authorizes, or raise `TokenError`."""
    payload = decode_token(ticket, expected_kind="stream")
    run_id = payload.get("run_id")
    if not isinstance(run_id, str):
        raise TokenError("ticket is missing run scope")
    return run_id
