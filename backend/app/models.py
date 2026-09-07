"""ORM models: users, credentials, research runs, agent traces, sources, claims."""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Role(str, enum.Enum):
    user = "user"
    admin = "admin"


class RunStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ClaimStatus(str, enum.Enum):
    supported = "supported"
    uncertain = "uncertain"
    refuted = "refuted"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    runs: Mapped[list[Run]] = relationship(back_populates="user", cascade="all, delete-orphan")


class RefreshToken(Base):
    """Rotating refresh tokens. Only the hash is stored; reuse revokes the family."""

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(36), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    query: Mapped[str] = mapped_column(Text)
    depth: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[RunStatus] = mapped_column(Enum(RunStatus), default=RunStatus.queued, index=True)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    citation_coverage: Mapped[float] = mapped_column(Float, default=0.0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    report_markdown: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    user: Mapped[User] = relationship(back_populates="runs")
    events: Mapped[list[RunEvent]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="RunEvent.seq"
    )
    sources: Mapped[list[Source]] = relationship(back_populates="run", cascade="all, delete-orphan")
    claims: Mapped[list[Claim]] = relationship(back_populates="run", cascade="all, delete-orphan")


class RunEvent(Base):
    """Append-only agent trace; replayed to late SSE subscribers."""

    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_seq", "run_id", "seq"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(64))
    phase: Mapped[str] = mapped_column(String(64))
    level: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="events")


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("run_id", "url", name="uq_source_run_url"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(16))  # S1, S2, ...
    url: Mapped[str] = mapped_column(String(2048))
    domain: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(512))
    snippet: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str | None] = mapped_column(String(64), default=None)
    credibility: Mapped[float] = mapped_column(Float, default=0.5)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[Run] = relationship(back_populates="sources")


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), index=True)
    subquestion: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(Enum(ClaimStatus), default=ClaimStatus.uncertain)
    support: Mapped[float] = mapped_column(Float, default=0.0)
    source_refs: Mapped[str] = mapped_column(String(512), default="")  # "S1,S3"
    rebuttals: Mapped[str | None] = mapped_column(Text, default=None)
    rounds: Mapped[int] = mapped_column(Integer, default=0)

    run: Mapped[Run] = relationship(back_populates="claims")


class AuditLog(Base):
    """Security-relevant events. Never contains credentials or raw tokens."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True, default=None)
    action: Mapped[str] = mapped_column(String(64), index=True)
    resource: Mapped[str | None] = mapped_column(String(128), default=None)
    ip: Mapped[str | None] = mapped_column(String(64), default=None)
    detail: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
