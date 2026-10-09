export UV_CACHE_DIR ?= /workspace/.cache/uv
.PHONY: install lint test test-unit migrate api worker up
install:
	uv sync --frozen
lint:
	uv run --no-sync ruff check packages tests scripts migrations
	uv run --no-sync ruff format --check packages tests scripts migrations
test:
	uv run --no-sync pytest -q
test-unit:
	uv run --no-sync pytest -q tests/test_security_storage.py
migrate:
	uv run --no-sync alembic upgrade head
api:
	uv run --no-sync wks api
worker:
	uv run --no-sync wks worker
up:
	docker compose up --build -d
