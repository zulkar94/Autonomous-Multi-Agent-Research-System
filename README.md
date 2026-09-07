# Autonomous Multi-Agent Research System

Six agents plan, search, extract, verify, debate and write. The output is a Markdown report where every factual sentence carries a citation to a source the system actually retrieved, plus a confidence score you can argue with.

The whole pipeline runs offline with no API keys, because the default LLM and search providers are deterministic mocks. Swap two environment variables to run it against Claude and a real search backend.

[![CI](https://github.com/zulkar94/Autonomous-Multi-Agent-Research-System/actions/workflows/ci.yml/badge.svg)](https://github.com/zulkar94/Autonomous-Multi-Agent-Research-System/actions/workflows/ci.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)

---

## What it does

```
question
   │
   ├─ Planner       decomposes into falsifiable sub-questions
   ├─ Searcher      retrieves, de-duplicates, caps per-domain dominance, scores credibility
   ├─ Analyst       extracts discrete claims, each bound to source refs (uncited claims are dropped)
   ├─ Verifier      judges claim vs. evidence, then adjusts for source independence and quality
   ├─ Critic ⇄ Analyst   adversarial debate rounds; concessions and rebuttals are recorded
   └─ Synthesizer   writes the report, and invented citation refs are stripped before you see it
   │
   └─→ report + claim ledger + citation coverage + confidence
```

Live agent traces stream to the browser over SSE. Every event is also persisted, so a client that connects late replays the full trace and then resumes streaming.

## Quick start

```bash
git clone https://github.com/zulkar94/Autonomous-Multi-Agent-Research-System.git && cd REPO
./scripts/dev.sh                 # installs deps, generates SECRET_KEY, starts the API
```

Open <http://localhost:8000>. Create an account (the first account becomes admin), ask a question, watch the agents work.

With Docker:

```bash
export SECRET_KEY="$(openssl rand -base64 48)"
docker compose up --build        # API on 127.0.0.1:8000, Postgres on the internal network
```

Working on the SPA:

```bash
make dev    # terminal 1: API on :8000
make web    # terminal 2: Vite on :5173, proxying /api to :8000
```

## Using real providers

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6

SEARCH_PROVIDER=tavily     # or brave
TAVILY_API_KEY=tvly-...
```

Everything else keeps working unchanged: the agents, the SSRF guard, the citation checks and the tests are provider-agnostic.

## Project layout

```
backend/app/
  agents/       planner, searcher, analyst, verifier, debate, synthesizer, orchestrator
  api/          auth, research runs and SSE, health/metrics/admin
  providers/    LLM (Anthropic + mock), search (Tavily/Brave/mock), hardened page fetcher
  security/     Argon2 hashing, JWT + refresh rotation, rate limiting, SSRF, prompt sanitising
  services/     run lifecycle, event bus, citation integrity
  static/       zero-build console served when no SPA bundle is present
frontend/src/   React + TypeScript client (trace ledger, claim ledger, report view)
tests/          48 tests: security primitives, auth flows, tenancy, fetcher, quotas, full pipeline
```

## API

All research endpoints require `Authorization: Bearer <access token>`.

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/v1/auth/register` | Create an account (first account gets the admin role) |
| POST | `/api/v1/auth/login` | Exchange credentials for an access + refresh pair |
| POST | `/api/v1/auth/refresh` | Rotate the refresh token; replay revokes the whole family |
| POST | `/api/v1/auth/logout` | Revoke a refresh token |
| GET | `/api/v1/auth/me` | Current account |
| POST | `/api/v1/research/runs` | Start a run (`query`, `depth` 1–3); returns 202 |
| GET | `/api/v1/research/runs` | List your runs |
| GET | `/api/v1/research/runs/{id}` | Run detail with claims, sources and report |
| GET | `/api/v1/research/runs/{id}/report` | Report as a Markdown download |
| POST | `/api/v1/research/runs/{id}/cancel` | Cancel an active run |
| DELETE | `/api/v1/research/runs/{id}` | Delete a run and its trace |
| POST | `/api/v1/research/runs/{id}/stream-ticket` | Mint a 60-second run-scoped SSE ticket |
| GET | `/api/v1/research/runs/{id}/events?ticket=…` | SSE trace: replay, then live |
| GET | `/healthz` `/readyz` `/metrics` | Liveness, readiness, Prometheus metrics |
| GET | `/admin/audit` | Audit log (admin role) |

Interactive docs at `/docs` in non-production environments; they are disabled when `APP_ENV=production`.

Example:

```bash
TOKEN=$(curl -sX POST localhost:8000/api/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"email":"you@example.org","password":"Str0ng-Passphrase!42"}' | jq -r .access_token)

RUN=$(curl -sX POST localhost:8000/api/v1/research/runs \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"Does structured code review reduce production defects?","depth":2}' | jq -r .id)

curl -s "localhost:8000/api/v1/research/runs/$RUN" -H "authorization: Bearer $TOKEN" | jq .confidence
```

## Security

Full detail in [SECURITY.md](SECURITY.md). In short:

- Argon2id password hashing with OWASP parameters and transparent rehash on upgrade.
- Short-lived JWT access tokens; opaque refresh tokens stored only as SHA-256 digests, rotated on every use, with family revocation on replay.
- SSE authorised by single-run, 60-second tickets, because `EventSource` cannot send headers.
- SSRF guard on every outbound URL: scheme and port allowlists, credential rejection, DNS results checked against private, loopback, link-local, CGNAT and cloud-metadata ranges, and redirects re-validated hop by hop.
- Retrieved web content is treated as data: markup stripped, injection patterns filtered, and everything fenced inside `-----UNTRUSTED-WEB-CONTENT-----` markers that the system prompts declare untrusted.
- Token-bucket rate limiting globally and a separate quota on run creation; request body size capped.
- Strict security headers (CSP, HSTS in production, nosniff, frame-deny, referrer and permissions policies), correlation IDs on every request, and secret redaction in logs.
- Cross-tenant reads return 404 rather than 403, so run IDs cannot be probed.
- Container runs as a non-root user, read-only root filesystem, all capabilities dropped.

## Testing and quality gates

```bash
make test        # pytest with coverage (fails under 75%)
make lint        # ruff check, ruff format --check, mypy
make security    # bandit + pip-audit
```

CI runs the suite on Python 3.11 and 3.12, plus bandit, pip-audit, gitleaks, a frontend type-check and build, a Docker image build, a container health smoke test and a Trivy image scan.

## Configuration

Every setting is an environment variable, documented in [.env.example](.env.example). Production is validated at startup: the process refuses to boot with a missing or short `SECRET_KEY`, with `DEBUG` enabled, with a wildcard CORS origin, or with the private-network SSRF exception turned on.

## Scaling notes

Runs execute as supervised asyncio tasks inside the API process, bounded by `MAX_CONCURRENT_RUNS`, with orphan recovery at startup and a graceful drain on shutdown. That is deliberate for a single node. To scale horizontally: point `DATABASE_URL` at Postgres, replace `RunManager.submit` with an enqueue onto Redis or RabbitMQ, run this module in a dedicated worker, and swap `MemoryRateLimiter` for a Redis implementation of the same `RateLimiterBackend` protocol.

## License

MIT. See [LICENSE](LICENSE).
