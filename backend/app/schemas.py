"""Request/response contracts. Validation happens here, never in handlers."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .config import get_settings

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(value: str) -> str:
    return _CONTROL_CHARS.sub("", value).strip()


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)

    @field_validator("password")
    @classmethod
    def _strength(cls, v: str) -> str:
        s = get_settings()
        if len(v) < s.password_min_length:
            raise ValueError(f"password must be at least {s.password_min_length} characters")
        classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
        if classes < 3:
            raise ValueError("password must mix lowercase, uppercase, digits and symbols")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=4096)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    role: str
    created_at: datetime


class RunCreate(BaseModel):
    query: str = Field(min_length=8, max_length=1000)
    depth: int = Field(default=2, ge=1, le=3)

    @field_validator("query")
    @classmethod
    def _sanitize(cls, v: str) -> str:
        v = _clean(v)
        if not v:
            raise ValueError("query must not be empty")
        return v


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    ref: str
    url: str
    domain: str
    title: str
    snippet: str
    credibility: float


class ClaimOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    subquestion: str
    text: str
    status: str
    support: float
    source_refs: str
    rebuttals: str | None
    rounds: int


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    seq: int
    agent: str
    phase: str
    level: str
    message: str
    created_at: datetime


class RunSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    query: str
    depth: int
    status: str
    confidence: float
    citation_coverage: float
    duration_ms: int
    created_at: datetime


class RunDetail(RunSummary):
    error: str | None = None
    tokens_used: int = 0
    report_markdown: str | None = None
    sources: list[SourceOut] = []
    claims: list[ClaimOut] = []


class StreamTicket(BaseModel):
    ticket: str
    expires_in: int


class HealthOut(BaseModel):
    status: str
    version: str
    environment: str


class ErrorOut(BaseModel):
    error: str
    detail: str | None = None
    request_id: str | None = None
