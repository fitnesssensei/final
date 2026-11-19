.PHONY: install run test lint format migrate up down logs

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

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f app
