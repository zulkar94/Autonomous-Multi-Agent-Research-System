# Operations runbook

## Deploy

1. Provision Postgres and set `DATABASE_URL=postgresql+asyncpg://…`.
2. Generate and inject `SECRET_KEY` (`openssl rand -base64 48`) from your secret manager — never from a file in the repo.
3. Set `APP_ENV=production`, `TRUSTED_HOSTS` to your real hostnames, and `CORS_ORIGINS` to the exact browser origin. Startup fails fast if any of these are unsafe.
4. Run behind a TLS-terminating proxy. The container listens on 8000 and honours `--proxy-headers`.
5. Scale with replicas, not in-container workers: runs are in-process asyncio tasks and the event bus is per-process.

## Health and telemetry

| Signal | Where | Meaning |
| --- | --- | --- |
| `/healthz` | liveness probe | process is up |
| `/readyz` | readiness probe | database round-trip succeeded |
| `/metrics` | Prometheus scrape (internal network only) | users, runs by status, in-flight runs |
| `ars_runs_active` | metric | in-flight runs on this replica; compare against `MAX_CONCURRENT_RUNS` |
| `run_failed` | log event | run raised; the exception type and message are in `error` |
| `prompt_injection_detected` | log event | retrieved page tried to issue instructions |
| `refresh_token_reuse` | log event | a rotated refresh token was replayed — treat as credential theft |

Logs are JSON in production, one line per request, correlated by `request_id`. The same id is returned to clients in `X-Request-ID`, so a user-reported error maps directly to a log line.

## Common situations

**Runs stuck in `running` after a crash.** They are marked failed automatically on next startup by `recover_orphaned_runs()`. No manual cleanup needed.

**Runs failing with "budget exhausted".** The run hit its token, call or wall-clock ceiling. Lower `MAX_SUBQUESTIONS` / `MAX_DEBATE_ROUNDS`, or raise `RUN_TIMEOUT_SECONDS` if the workload legitimately needs longer.

**Reports with low citation coverage.** The synthesizer wrote substantive sentences without refs, or refs were stripped as invalid. Check for `Removed N hallucinated citation refs` in the trace; if it recurs, the model or prompt needs attention, not the scorer.

**Repeated `search_result_blocked`.** The search backend is returning URLs that fail egress policy. That is the guard working. Investigate the backend before considering any policy change, and never enable `ALLOW_PRIVATE_NETWORK` in a deployed environment.

**Suspected token theft.** Deactivate the user (`users.is_active = false`), which invalidates access on the next request and blocks refresh. Rotating `SECRET_KEY` invalidates every access token and stream ticket globally; refresh tokens survive a key rotation because they are opaque, so revoke them in the database as well.

## Backup and recovery

Back up Postgres on your normal schedule. The only irreplaceable data is `runs`, `run_events`, `sources` and `claims`. Nothing in the database is a secret: passwords are Argon2id digests and refresh tokens are SHA-256 digests, so a database restore never resurrects a usable credential.
