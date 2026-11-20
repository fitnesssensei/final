"""Ошибки БД превращаются в 503 с подсказкой, а не в 500 без объяснения."""

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import ProgrammingError

from keitaro_wrapper.api.deps import SessionDep
from keitaro_wrapper.api.errors import DATABASE_NOT_READY, handle_database_not_ready
from keitaro_wrapper.db import OperationLog
from keitaro_wrapper.main import create_app, lifespan

VALID_ENV = {
    "KEITARO_BASE_URL": "https://tlgk.host",
    "KEITARO_API_KEY": "test-key",
    "KEITARO_DOMAIN_ID": "1813",
    "KEITARO_GROUP_ID": "10332",
    "KEITARO_SOURCE_ID": "836",
}


def set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    for name, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


async def test_missing_migrations_return_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Пустая SQLite без `alembic upgrade` — 503 с подсказкой, а не 500.

    SQLite отвечает `OperationalError: no such table`, Postgres —
    `ProgrammingError: relation ... does not exist`; оба обязаны дать 503.
    """
    database = tmp_path / "empty.db"
    set_env(monkeypatch, DATABASE_URL=f"sqlite+aiosqlite:///{database}")
    app = create_app()

    @app.get("/db-probe")
    async def probe(session: SessionDep) -> dict[str, int]:
        count = await session.scalar(select(func.count()).select_from(OperationLog))
        return {"count": count or 0}

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/db-probe")

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_NOT_READY}
    assert "make migrate" in response.json()["detail"]


async def test_connection_refused_returns_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Недоступный Postgres: asyncpg кидает ConnectionRefusedError, а не OperationalError."""
    set_env(monkeypatch, DATABASE_URL="sqlite+aiosqlite:///:memory:")
    app = create_app()

    @app.get("/broken-db")
    async def broken_db() -> None:
        raise ConnectionRefusedError(61, "Connect call failed")

    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/broken-db")

    assert response.status_code == 503
    assert response.json() == {"detail": DATABASE_NOT_READY}


async def test_programming_error_does_not_leak_sql() -> None:
    """Postgres-ошибка миграции: 503, текст драйвера не уходит клиенту."""
    request = SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/campaigns"))
    error = ProgrammingError(
        "SELECT 1",
        {},
        Exception('relation "operation_log" does not exist'),
    )

    response = await handle_database_not_ready(request, error)

    body = json.loads(response.body)
    assert response.status_code == 503
    assert body == {"detail": DATABASE_NOT_READY}
    assert "operation_log" not in body["detail"]
