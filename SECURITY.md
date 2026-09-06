# Security

## Reporting a vulnerability

Open a private security advisory on GitHub, or email the maintainer listed in `pyproject.toml`. Please do not file a public issue for an exploitable bug. Expect an acknowledgement within three working days.

## Threat model

The system takes an untrusted question from an authenticated user, fetches untrusted content from the open web, and feeds both to a language model. Three attack surfaces follow from that:

1. **The user attacking the platform** — credential stuffing, tenancy escape, resource exhaustion.
2. **The web attacking the agents** — prompt injection inside retrieved pages, SSRF via crafted URLs, oversized or malicious responses.
3. **The platform leaking** — secrets in logs, provider keys reaching the browser, reports citing sources that were never retrieved.

## Controls

### Authentication and session handling

| Control | Implementation |
| --- | --- |
| Password storage | Argon2id, `time_cost=2`, `memory_cost=19456`, `parallelism=1`; rehashed transparently when parameters change |
| Password policy | 12 characters minimum, at least three character classes |
| Access tokens | HS256 JWT, 15-minute lifetime, typed (`access`/`stream`), issuer-checked, `exp`/`iat`/`sub`/`jti` required |
| Refresh tokens | Opaque 256-bit secrets; only the SHA-256 digest is stored; rotated on every use |
| Replay defence | Using a rotated refresh token revokes every token in its family |
| Brute force | Five failed logins lock the account for 15 minutes; counters persist across the failed request |
| Enumeration | Unknown users are verified against a real dummy hash, and unknown-user, wrong-password and inactive-account all return the same 401 body |
| SSE authorisation | `EventSource` cannot set headers, so streaming uses a 60-second ticket scoped to one run id; a mismatched ticket is 403, a forged one 401 |

### Multi-tenancy

Every run query is filtered by `user_id` at the SQL level. A run belonging to another account returns **404**, not 403, so identifiers cannot be probed. The admin audit endpoint requires the `admin` role, which only the first registered account receives by default.

### Egress control (SSRF)

`app/security/ssrf.py` validates every outbound URL before a connection is opened:

- scheme allowlist (`http`, `https`) and port allowlist (80, 443);
- credentials embedded in the URL are rejected;
- the hostname is resolved and **every** returned address is checked against private, loopback, link-local, multicast, reserved and CGNAT ranges;
- cloud metadata hosts and addresses (169.254.169.254, 100.100.100.200, `metadata.google.internal`, and others) are blocked outright;
- redirects are not followed automatically — each hop is re-validated, capped at three;
- responses are capped at `FETCH_MAX_BYTES` and restricted to text-like content types.

`ALLOW_PRIVATE_NETWORK=true` exists for local testing and is rejected at startup when `APP_ENV=production`.

### Prompt injection

Retrieved content is data, never instruction:

- `<script>`/`<style>` blocks and all markup are stripped before the text reaches a prompt;
- known override patterns ("ignore all previous instructions", "reveal your system prompt", forged `<system>` tags, exfiltration phrasing) are detected, replaced with `[filtered]`, and logged;
- the fence marker itself is filtered out of content so it cannot be forged;
- all untrusted text is wrapped in `-----UNTRUSTED-WEB-CONTENT-----` delimiters, and every agent's system prompt states that content inside the fence carries no authority.

### Output integrity

- The Analyst drops any claim without a valid source ref rather than repairing it.
- The Synthesizer's output is scanned and any `[Sn]` marker that does not match a retrieved source is deleted before the report is stored.
- Citation coverage — the share of substantive sentences carrying a valid citation — is computed and returned with every report.
- The browser renders Markdown through a local converter that escapes the source **before** emitting any tag and permits only `http`/`https` link targets.

### Abuse and availability

- Token-bucket rate limiting per identity, plus a separate hourly quota on run creation.
- Request bodies capped at 256 KB.
- Each run carries a budget: maximum tokens, maximum LLM calls and a wall-clock ceiling, checked before every model call.
- `MAX_CONCURRENT_RUNS` bounds parallel work; SSE subscribers that fall behind are dropped rather than allowed to stall a run.
- Runs interrupted by a process restart are marked failed at startup instead of hanging in `running`.

### Transport and headers

`Content-Security-Policy`, `Strict-Transport-Security` (production), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy` and a restrictive `Permissions-Policy` are set on every response. CORS is origin-pinned with credentials disabled — the API is bearer-token only, so there is no cookie surface and therefore no CSRF surface.

### Secrets and logging

Provider keys live only in the server environment and never reach the browser. Logs are structured JSON with a correlation ID per request, and a redaction filter strips API-key-shaped strings, bearer tokens and `password`/`secret`/`token` assignments before anything is emitted. The audit log records security-relevant actions and never stores credentials or raw tokens.

### Supply chain

Dependencies are pinned. CI runs `bandit`, `pip-audit --strict`, `npm audit`, `gitleaks` and a Trivy image scan; Dependabot opens grouped weekly updates for pip and npm, monthly for Actions and Docker. The runtime image runs as UID 10001 with a read-only root filesystem and all Linux capabilities dropped.

## Known limitations

- Rate limiting and the event bus are per-process. Multi-replica deployments need the Redis backend described in `README.md`.
- Schema creation uses `create_all`. Add Alembic before the first production migration.
- `TRUSTED_HOSTS` defaults to `*`; set it to your real hostnames behind a proxy.
- Credibility scoring is a heuristic prior, not an authority ranking. It informs confidence; it does not replace the Verifier.
