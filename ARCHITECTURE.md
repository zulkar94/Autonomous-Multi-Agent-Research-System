# Architecture

## Request and run flow

```
browser ──HTTP──> FastAPI ──> RunManager ──> Orchestrator ──> agents ──> providers
   ▲                 │             │                              │
   └────SSE──────────┘             └── persists events, sources, claims, report ──> DB
```

A `POST /research/runs` writes the run row, commits it, and hands the id to `RunManager`, which spawns a supervised asyncio task. The HTTP request returns 202 immediately. The task owns its own database sessions, so a disconnected client never affects it.

## Agent pipeline

| Stage | Agent | Contract |
| --- | --- | --- |
| Decomposition | Planner | question → sub-questions + search queries (JSON) |
| Retrieval | Searcher | queries → de-duplicated, egress-checked, credibility-scored sources |
| Extraction | Analyst | sub-question + sources → claims bound to source refs |
| Verification | Verifier | claim + cited evidence → verdict and support score |
| Debate | Critic ⇄ Analyst, moderated | challenges, rebuttals, concessions, score penalties |
| Synthesis | Synthesizer | verified claims → cited Markdown, confidence, coverage |

Design choices worth stating:

- **Claims are the unit of truth, not paragraphs.** A claim without a valid ref is discarded at extraction, which is why hallucinated citations cannot enter the report through the front door — and the ref scrubber closes the back door.
- **The verifier does not trust the model's own score.** Raw support is multiplied by a source-independence factor (distinct domains among the citations) and a quality factor (mean credibility), then demoted to `uncertain` if it lands below `MIN_CLAIM_SUPPORT`.
- **Debate converges early.** A round that produces no medium- or high-severity challenge ends the debate, so cheap claims settle fast and contested ones absorb the budget.
- **Budgets are enforced before the call, not after.** `Budget` caps tokens, call count and wall-clock time, and every agent checks it inside `Agent.think`.

## Concurrency and lifecycle

- `MAX_CONCURRENT_RUNS` semaphore bounds parallel runs per process.
- Cancellation is cooperative: `Orchestrator._checkpoint()` runs between stages, and the debate loop checks between rounds.
- `asyncio.wait_for` enforces `RUN_TIMEOUT_SECONDS` as a hard outer bound.
- Shutdown signals every run, waits for them to persist, then disposes the engine.
- `recover_orphaned_runs()` marks anything left `running` by a crashed process as failed at startup.

## Event streaming

Events are published to an in-process `EventBus` **and** appended to `run_events`. An SSE subscriber first receives the persisted history, then live events, skipping any sequence number it already replayed. Subscriber queues are bounded at 256; a consumer that fills its queue is unsubscribed rather than allowed to block the publisher. Heartbeat comments every 15 seconds keep proxies from closing idle streams.

## Data model

```
users ──< refresh_tokens
  └──< runs ──< run_events      (append-only agent trace, ordered by seq)
             ├─< sources        (ref, url, domain, credibility, content hash)
             └─< claims         (text, status, support, source_refs, rebuttals, rounds)
audit_log                        (security-relevant actions, no credentials)
```

Runs cascade-delete their trace, sources and claims. `sources` is unique on `(run_id, url)`.

## Extension points

| Want to… | Do this |
| --- | --- |
| Add an LLM vendor | Subclass `LLMProvider`, return it from `get_llm_provider()` |
| Add a search backend | Subclass `SearchProvider`; `_filter()` already enforces egress policy |
| Add an agent | Subclass `Agent`, wire it into `Orchestrator.run` between checkpoints |
| Distribute runs | Replace `RunManager.submit` with a queue enqueue; run the module as a worker |
| Share rate limits | Implement `RateLimiterBackend` against Redis and rebind `limiter` |
| Change scoring | `VerifierAgent._verify_one` (support) and `SynthesizerAgent._confidence` (blend) |

## Why these boundaries

The ORM never reaches the agents — they exchange plain dataclasses in `agents/types.py`. That keeps the pipeline unit-testable without a database, which is why the full-pipeline test runs in milliseconds against mock providers and no I/O. Persistence happens once, at the edge, in `RunManager._persist`.
