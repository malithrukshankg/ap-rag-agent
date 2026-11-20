# Reviewer entry points (§6, §7). Each target is a thin wrapper over uv, so the same
# commands work without make (e.g. on Windows): copy the recipe line.

.PHONY: install lock ingest test test-live lint format run docker-build

install:
	uv sync --frozen

# Re-resolve dependencies and refresh both lock files.
lock:
	uv lock
	uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.lock

ingest:
	uv run ap ingest

# Stable tier: no key, no network.
test:
	uv run pytest -m "not live"

# Live tier: needs ANTHROPIC_API_KEY.
test-live:
	uv run pytest -m live

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy

format:
	uv run ruff check --fix .
	uv run ruff format .

# Single worker by design: SQLite single writer (architecture §0.3, §12).
run:
	uv run uvicorn ap_agent.api.app:app --workers 1 --host 127.0.0.1 --port 8000

docker-build:
	docker build -t ap-rag-agent .
