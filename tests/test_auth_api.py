"""Authentication flows: registration policy, lockout, rotation, replay defence."""

from __future__ import annotations

import httpx
from tests.conftest import PASSWORD


async def test_register_login_me(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": "a@example.org", "password": PASSWORD}
    )
    assert response.status_code == 201
    assert response.json()["role"] == "admin"  # first user bootstraps the admin

    login = await client.post(
        "/api/v1/auth/login", json={"email": "a@example.org", "password": PASSWORD}
    )
    assert login.status_code == 200
    tokens = login.json()

    me = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert me.json()["email"] == "a@example.org"


async def test_weak_password_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": "b@example.org", "password": "short"}
    )
    assert response.status_code == 422


async def test_duplicate_registration_is_generic(client: httpx.AsyncClient) -> None:
    payload = {"email": "c@example.org", "password": PASSWORD}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    duplicate = await client.post("/api/v1/auth/register", json=payload)
    assert duplicate.status_code == 409
    assert "exists" not in duplicate.text.lower()


async def test_unknown_user_and_bad_password_look_identical(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "d@example.org", "password": PASSWORD}
    )
    bad = await client.post(
        "/api/v1/auth/login", json={"email": "d@example.org", "password": "Wrong-Password-1!"}
    )
    unknown = await client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.org", "password": PASSWORD}
    )
    assert bad.status_code == unknown.status_code == 401
    assert bad.json()["error"] == unknown.json()["error"]


async def test_account_locks_after_repeated_failures(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "e@example.org", "password": PASSWORD}
    )
    for _ in range(5):
        await client.post(
            "/api/v1/auth/login", json={"email": "e@example.org", "password": "Wrong-Password-1!"}
        )
    locked = await client.post(
        "/api/v1/auth/login", json={"email": "e@example.org", "password": PASSWORD}
    )
    assert locked.status_code == 429


async def test_refresh_rotation_and_reuse_revokes_family(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/auth/register", json={"email": "f@example.org", "password": PASSWORD}
    )
    tokens = (
        await client.post(
            "/api/v1/auth/login", json={"email": "f@example.org", "password": PASSWORD}
        )
    ).json()

    rotated = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert rotated.status_code == 200
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != tokens["refresh_token"]

    replay = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert replay.status_code == 401

    # Family revocation: the rotated token is dead too.
    after = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": new_tokens["refresh_token"]}
    )
    assert after.status_code == 401


async def test_protected_route_requires_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (
        await client.get("/api/v1/auth/me", headers={"Authorization": "Bearer not-a-token"})
    ).status_code == 401


async def test_security_headers_present(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "Content-Security-Policy" in response.headers
    assert response.headers["X-Request-ID"]
