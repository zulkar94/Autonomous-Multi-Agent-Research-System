"""End-to-end run lifecycle over HTTP, including tenancy isolation."""

from __future__ import annotations

import asyncio

import httpx
from tests.conftest import PASSWORD

QUERY = "Does structured code review reduce production defects?"


async def _wait_for_terminal(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    run_id: str,
    timeout: float = 60.0,  # noqa: ASYNC109 - polling helper, not a cancel scope
) -> dict:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        response = await client.get(f"/api/v1/research/runs/{run_id}", headers=headers)
        body = response.json()
        if body["status"] in ("completed", "failed", "cancelled"):
            return body
        await asyncio.sleep(0.05)
    raise AssertionError("run did not reach a terminal state in time")


async def test_run_completes_with_report_sources_and_claims(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth
    )
    assert created.status_code == 202
    run_id = created.json()["id"]

    run = await _wait_for_terminal(client, auth, run_id)
    assert run["status"] == "completed", run.get("error")
    assert run["sources"] and run["claims"]
    assert run["confidence"] > 0
    assert "## References" in run["report_markdown"]

    report = await client.get(f"/api/v1/research/runs/{run_id}/report", headers=auth)
    assert report.status_code == 200
    assert report.headers["content-type"].startswith("text/markdown")


async def test_runs_are_scoped_to_their_owner(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    created = await client.post(
        "/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth
    )
    run_id = created.json()["id"]

    await client.post(
        "/api/v1/auth/register", json={"email": "intruder@example.org", "password": PASSWORD}
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": "intruder@example.org", "password": PASSWORD}
    )
    other = {"Authorization": f"Bearer {login.json()['access_token']}"}

    assert (await client.get(f"/api/v1/research/runs/{run_id}", headers=other)).status_code == 404
    deleted = await client.delete(f"/api/v1/research/runs/{run_id}", headers=other)
    assert deleted.status_code == 404
    assert (await client.get("/api/v1/research/runs", headers=other)).json() == []


async def test_run_requires_authentication(client: httpx.AsyncClient) -> None:
    response = await client.post("/api/v1/research/runs", json={"query": QUERY})
    assert response.status_code == 401


async def test_query_validation(client: httpx.AsyncClient, auth: dict[str, str]) -> None:
    short = await client.post("/api/v1/research/runs", json={"query": "hi"}, headers=auth)
    assert short.status_code == 422
    bad_depth = await client.post(
        "/api/v1/research/runs", json={"query": QUERY, "depth": 9}, headers=auth
    )
    assert bad_depth.status_code == 422


async def test_stream_ticket_is_scoped_to_one_run(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    first = (
        await client.post("/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth)
    ).json()["id"]
    second = (
        await client.post(
            "/api/v1/research/runs", json={"query": QUERY + " variant", "depth": 1}, headers=auth
        )
    ).json()["id"]

    ticket = (
        await client.post(f"/api/v1/research/runs/{first}/stream-ticket", headers=auth)
    ).json()["ticket"]

    cross = await client.get(f"/api/v1/research/runs/{second}/events?ticket={ticket}")
    assert cross.status_code == 403

    forged = await client.get(f"/api/v1/research/runs/{first}/events?ticket=not-a-real-ticket")
    assert forged.status_code == 401

    await _wait_for_terminal(client, auth, first)
    await _wait_for_terminal(client, auth, second)


async def test_event_stream_replays_agent_trace(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    run_id = (
        await client.post("/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth)
    ).json()["id"]
    await _wait_for_terminal(client, auth, run_id)

    ticket = (
        await client.post(f"/api/v1/research/runs/{run_id}/stream-ticket", headers=auth)
    ).json()["ticket"]
    stream = await client.get(f"/api/v1/research/runs/{run_id}/events?ticket={ticket}")
    assert stream.status_code == 200
    body = stream.text
    assert "event: message" in body
    assert "planner" in body and "synthesizer" in body
    assert body.rstrip().endswith("\n\n") or "event: end" in body


async def test_delete_removes_the_run(client: httpx.AsyncClient, auth: dict[str, str]) -> None:
    run_id = (
        await client.post("/api/v1/research/runs", json={"query": QUERY, "depth": 1}, headers=auth)
    ).json()["id"]
    await _wait_for_terminal(client, auth, run_id)

    assert (await client.delete(f"/api/v1/research/runs/{run_id}", headers=auth)).status_code == 204
    assert (await client.get(f"/api/v1/research/runs/{run_id}", headers=auth)).status_code == 404


async def test_health_and_metrics(client: httpx.AsyncClient) -> None:
    assert (await client.get("/healthz")).json()["status"] == "ok"
    assert (await client.get("/readyz")).json()["status"] == "ready"
    metrics = await client.get("/metrics")
    assert "ars_runs_total" in metrics.text


async def test_admin_audit_requires_admin_role(
    client: httpx.AsyncClient, auth: dict[str, str]
) -> None:
    # The fixture user is the first registered account, therefore admin.
    assert (await client.get("/admin/audit", headers=auth)).status_code == 200

    await client.post(
        "/api/v1/auth/register", json={"email": "plain@example.org", "password": PASSWORD}
    )
    login = await client.post(
        "/api/v1/auth/login", json={"email": "plain@example.org", "password": PASSWORD}
    )
    plain = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/admin/audit", headers=plain)).status_code == 403
