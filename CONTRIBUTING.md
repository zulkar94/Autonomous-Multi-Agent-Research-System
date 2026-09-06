# Contributing

## Setup

```bash
python3 -m pip install -r requirements-dev.txt
cd frontend && npm install
export PYTHONPATH=backend
```

`make help` lists everything else.

## Before opening a pull request

```bash
make fmt        # ruff --fix and format
make lint       # ruff check, format check, mypy
make test       # pytest with coverage (gate: 75%)
make security   # bandit, pip-audit
```

CI runs the same gates on Python 3.11 and 3.12, plus the frontend build and an image scan.

## House rules

- **New behaviour needs a test.** Security-relevant behaviour needs a test that fails without the control — see `tests/test_security.py` and the tenancy tests for the pattern.
- **Type everything new.** `mypy backend/app` must stay clean.
- **Never let untrusted text into a prompt unfenced.** Use `wrap_untrusted()`; never interpolate retrieved content directly.
- **Never fetch a URL without `validate_url()`.** The fetcher already does it; anything new must too.
- **Keep the ORM out of the agents.** Agents exchange dataclasses; persistence belongs in `services/runs.py`.
- **Comments explain why, not what.** If a line needs a comment to say what it does, rewrite the line.
- Conventional commit subjects (`feat:`, `fix:`, `docs:`, `test:`, `chore:`) keep the history greppable.

## Adding an agent

1. Subclass `Agent` in `backend/app/agents/`, set `name` and `role`.
2. Add its system prompt to `prompts.py`, including the `GUARD` clause if it will ever see retrieved content.
3. Wire it into `Orchestrator.run` between `_checkpoint()` calls.
4. Emit trace events with `self.say(...)` so the UI shows the work.
5. Test it against `MockLLMProvider`, adding a branch to `MockLLMProvider._render` for the new tag.
