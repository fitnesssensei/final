.PHONY: install run test lint format migrate migrate-down up down logs

install:
	uv sync

run:
	uv run uvicorn keitaro_wrapper.main:app --reload --port 8000

test:
	uv run pytest -q

lint:
	uv run ruff check .

format:
	uv run ruff format .

migrate:
	uv run alembic upgrade head

migrate-down:
	uv run alembic downgrade -1

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f app
