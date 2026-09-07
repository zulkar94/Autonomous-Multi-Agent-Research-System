"""Async SQLAlchemy engine, session factory and declarative base."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from .config import get_settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


_settings = get_settings()

_connect_args: dict[str, object] = {}
_kwargs: dict[str, object] = {}
if _settings.database_url.startswith("sqlite"):
    _connect_args["check_same_thread"] = False
    if ":memory:" in _settings.database_url:
        _kwargs["poolclass"] = StaticPool
    else:
        path = _settings.database_url.split("///")[-1]
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.db_echo,
    pool_pre_ping=True,
    connect_args=_connect_args,
    **_kwargs,
)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def init_db() -> None:
    """Create tables. Production deployments should use Alembic migrations."""
    from . import models  # noqa: F401  (register mappers)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a transactional session."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager for background tasks that own their transaction."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
