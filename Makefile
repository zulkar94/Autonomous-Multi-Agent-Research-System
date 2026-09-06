.DEFAULT_GOAL := help
PY ?= python3
export PYTHONPATH := backend

help: ## List targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

install: ## Install backend and frontend dependencies
	$(PY) -m pip install -r requirements-dev.txt
	cd frontend && npm install

dev: ## Run the API with reload (serves the console at http://localhost:8000)
	$(PY) -m uvicorn app.main:app --reload --port 8000

web: ## Run the SPA dev server against a local API
	cd frontend && npm run dev

test: ## Run the test suite with coverage
	$(PY) -m pytest --cov=app --cov-report=term-missing

lint: ## Lint and type-check
	$(PY) -m ruff check backend tests
	$(PY) -m ruff format --check backend tests
	$(PY) -m mypy backend/app

fmt: ## Auto-format and auto-fix
	$(PY) -m ruff check --fix backend tests
	$(PY) -m ruff format backend tests

security: ## Static security scan and dependency audit
	$(PY) -m bandit -c pyproject.toml -r backend/app
	$(PY) -m pip_audit -r requirements.txt

build: ## Build the SPA bundle served by the API
	cd frontend && npm run build

docker: ## Build the production image
	docker build -t ars:local .

up: ## Start the full stack (API + Postgres)
	docker compose up --build

clean: ## Remove caches and build output
	rm -rf .pytest_cache .ruff_cache .mypy_cache htmlcov .coverage frontend/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

.PHONY: help install dev web test lint fmt security build docker up clean
