#!/usr/bin/env bash
# Bring up a local development environment with offline providers.
set -euo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  cp .env.example .env
  printf 'SECRET_KEY=%s\n' "$(openssl rand -base64 48 | tr -d '\n')" >> .env
  echo "Wrote .env with a generated SECRET_KEY."
fi

export PYTHONPATH="${PWD}/backend"
python3 -m pip install -q -r requirements-dev.txt
exec python3 -m uvicorn app.main:app --reload --port "${PORT:-8000}"
