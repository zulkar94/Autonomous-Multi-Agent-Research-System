"""JWT issuance/verification and opaque refresh-token handling.

Access tokens are short-lived JWTs. Refresh tokens are opaque 256-bit secrets;
only their SHA-256 digest is persisted, and each use rotates the token within a
family so that replay of a consumed token revokes the whole family.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt

from ..config import get_settings

TokenKind = Literal["access", "stream"]


class TokenError(Exception):
    """Raised when a token is malformed, expired or of the wrong type."""


def _now() -> datetime:
    return datetime.now(UTC)


def create_token(
    subject: str,
    kind: TokenKind = "access",
    ttl_seconds: int | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    s = get_settings()
    if ttl_seconds is None:
        ttl_seconds = (
            s.access_token_ttl_seconds if kind == "access" else s.stream_ticket_ttl_seconds
        )
    now = _now()
    payload: dict[str, Any] = {
        "sub": subject,
        "typ": kind,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl_seconds)).timestamp()),
        "jti": str(uuid.uuid4()),
        "iss": s.app_name,
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, s.secret_key, algorithm=s.jwt_algorithm)


def decode_token(
    token: str,
    expected_kind: TokenKind = "access",
) -> dict[str, Any]:
    s = get_settings()
    try:
        payload = jwt.decode(
            token,
            s.secret_key,
            algorithms=[s.jwt_algorithm],
            issuer=s.app_name,
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
    except jwt.PyJWTError as exc:  # expired, bad signature, wrong issuer
        raise TokenError(str(exc)) from exc
    if payload.get("typ") != expected_kind:
        raise TokenError("unexpected token type")
    return payload


def generate_refresh_token() -> tuple[str, str]:
    """Return (plaintext, sha256 digest). Plaintext is never stored."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_refresh_token(raw)


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
