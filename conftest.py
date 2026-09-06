"""Test harness: isolated in-memory database, offline providers, auth helpers."""

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

os.environ.update(
    APP_ENV="test",
    SECRET_KEY="test-secret-key-that-is-long-enough-for-hs256-usage",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    LLM_PROVIDER="mock",
    SEARCH_PROVIDER="mock",
    RATE_LIMIT_ENABLED="true",
    RATE_LIMIT_REQUESTS="10000",
    RATE_LIMIT_RUN_CREATION="1000",
    MAX_DEBATE_ROUNDS="2",
)

import httpx  # noqa: E402
from httpx import ASGITransport  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security.ratelimit import limiter  # noqa: E402
from app.services.runs import manager  # noqa: E402

pytest_plugins = ("pytest_asyncio",)


@pytest.fixture(scope="session")
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
async def _database() -> AsyncIterator[None]:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    if hasattr(limiter, "reset"):
        limiter.reset()
    yield
    # Background runs must finish before the schema disappears underneath them.
    await manager.shutdown(timeout=15.0)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


PASSWORD = "Str0ng-Passphrase!42"


@pytest.fixture
async def auth(client: httpx.AsyncClient) -> dict[str, str]:
    """Register + log in a user; returns an Authorization header mapping."""
    email = "researcher@example.org"
    await client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD})
    response = await client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    token = response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
