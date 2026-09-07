"""Quota enforcement on the expensive run-creation path."""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.security.ratelimit import limiter

QUERY = "Does structured code review reduce production defects?"


@pytest.fixture
def tight_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "rate_limit_run_creation", 2, raising=False)
    monkeypatch.setattr(settings, "rate_limit_run_window_seconds", 3600, raising=False)
    if hasattr(limiter, "reset"):
        limiter.reset()


async def test_run_quota_returns_429_with_retry_after(
    client: httpx.AsyncClient, auth: dict[str, str], tight_quota: None
) -> None:
    for _ in range(2):
        ok = await client.post(
            "/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth
        )
        assert ok.status_code == 202

    blocked = await client.post(
        "/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["Retry-After"]) > 0


async def test_oversized_payload_is_rejected(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    response = await client.post(
        "/api/v1/research/runs", json={"query": "x" * 300_000}, headers=auth
    )
    assert response.status_code in (413, 422)
