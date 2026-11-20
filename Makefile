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
	@echo "Миграций пока нет: таблицы и Alembic появятся на шаге 3." >&2
	@echo "До этого приложению БД не нужна, alembic.ini в проекте отсутствует." >&2
	@exit 1

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f app
