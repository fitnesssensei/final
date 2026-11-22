"""Тесты служебных ручек: живость, готовность и сборка OpenAPI-схемы."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from keitaro_wrapper import __version__
from keitaro_wrapper.db import Base, create_session_factory
from keitaro_wrapper.main import create_app

# Фикстура `client` — общая, в tests/conftest.py


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_openapi_schema_contains_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
    assert "/health" in schema["paths"]
    # Readiness — отдельная ручка: liveness и готовность не должны сливаться
    assert "/ready" in schema["paths"]
    # Literal["ok"] в схеме ответа: единственный возможный статус
    status_schema = schema["components"]["schemas"]["HealthResponse"]["properties"]["status"]
    assert status_schema["const"] == "ok"


# --- readiness: приложение + база -------------------------------------------------


@asynccontextmanager
async def app_with_engine(engine: AsyncEngine) -> AsyncIterator[httpx.AsyncClient]:
    """Приложение без `lifespan`: фабрика сессий подставляется явно.

    `lifespan` читает `.env`, а readiness проверяется отдельно от настроек —
    так тест не зависит от машины разработчика.
    """
    app = create_app()
    app.state.session_factory = create_session_factory(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def sqlite_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Файловая SQLite с накатанной схемой: `SELECT 1` должен пройти."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ready.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


async def test_ready_reports_ok_when_database_answers(sqlite_engine: AsyncEngine) -> None:
    """БД отвечает — readiness 200, в теле видно, что проверена именно база."""
    async with app_with_engine(sqlite_engine) as http_client:
        response = await http_client.get("/ready")

    assert response.status_code == 200
    body = response.json()
    assert body == {"status": "ready", "version": __version__, "database": "ok"}


async def test_ready_returns_503_when_database_unavailable(tmp_path: Path) -> None:
    """БД недоступна — 503 с подсказкой, а не 500 «что-то сломалось».

    Адрес указывает в несуществующий каталог: драйвер падает на подключении,
    поэтому проверка не зависит от того, запущен ли Postgres на машине.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing/ready.db")
    try:
        async with app_with_engine(engine) as http_client:
            response = await http_client.get("/ready")
    finally:
        await engine.dispose()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["database"] == "unavailable"
    assert "make migrate" in body["detail"]
