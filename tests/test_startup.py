"""Тесты старта: с неверным `.env` приложение падает сразу, а не на первом запросе."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select, text

from keitaro_wrapper.api.deps import get_keitaro_client, get_session
from keitaro_wrapper.db import Base, OperationLog, OperationType
from keitaro_wrapper.keitaro import KeitaroClient
from keitaro_wrapper.logging_setup import configure_logging
from keitaro_wrapper.main import create_app, lifespan

VALID_ENV = {
    "KEITARO_BASE_URL": "https://tlgk.host",
    "KEITARO_API_KEY": "test-key",
    "KEITARO_DOMAIN_ID": "1813",
    "KEITARO_GROUP_ID": "10332",
    "KEITARO_SOURCE_ID": "836",
}


def set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Готовит валидное окружение, точечно подменяя нужные переменные."""
    for name, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


@asynccontextmanager
async def running_app() -> AsyncIterator[httpx.AsyncClient]:
    """Поднимает приложение вместе с `lifespan` — так же, как это делает uvicorn."""
    app = create_app()
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_app_starts_with_valid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch)

    async with running_app() as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_app_fails_fast_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой ключ — ошибка старта, иначе `/health` зелёный, а Keitaro отвечает 401."""
    set_env(monkeypatch, KEITARO_API_KEY="")

    with pytest.raises(RuntimeError, match="keitaro_api_key"):
        async with running_app():
            pass


async def test_app_fails_fast_on_http_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, KEITARO_BASE_URL="http://tlgk.host")

    with pytest.raises(RuntimeError, match="https"):
        async with running_app():
            pass


async def test_startup_error_does_not_leak_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщение об ошибке перечисляет поля, но не печатает значения из `.env`."""
    set_env(monkeypatch, KEITARO_API_KEY="SECRET42", KEITARO_BASE_URL="http://tlgk.host")

    with pytest.raises(RuntimeError) as exc_info:
        async with running_app():
            pass

    assert "keitaro_base_url" in str(exc_info.value)
    assert "SECRET42" not in str(exc_info.value)


async def test_lifespan_creates_and_closes_keitaro_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Клиент поднимается на старте и закрывается на остановке — соединения не текут."""
    set_env(monkeypatch)
    app = create_app()

    async with lifespan(app):
        client = app.state.keitaro
        assert isinstance(client, KeitaroClient)
        assert client.api_root == "https://tlgk.host/admin_api/v1"
        assert not client.is_closed

    assert client.is_closed


async def test_dependency_returns_client_from_app_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ручки будут получать тот же клиент, что создан в `lifespan`."""
    set_env(monkeypatch)
    app = create_app()

    async with lifespan(app):
        request = SimpleNamespace(app=app)

        assert get_keitaro_client(request) is app.state.keitaro


async def test_lifespan_creates_database_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Движок и фабрика сессий появляются на старте и работают."""
    set_env(monkeypatch, DATABASE_URL="sqlite+aiosqlite:///:memory:")
    app = create_app()

    async with lifespan(app):
        async with app.state.session_factory() as session:
            assert (await session.execute(text("select 1"))).scalar() == 1
        assert app.state.engine.url.get_backend_name() == "sqlite"


async def test_session_dependency_rolls_back_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Исключение в ручке откатывает журнал: полстроки не остаётся."""
    set_env(monkeypatch, DATABASE_URL="sqlite+aiosqlite:///:memory:")
    app = create_app()

    async with lifespan(app):
        async with app.state.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        generator = get_session(SimpleNamespace(app=app))
        session = await anext(generator)
        session.add(
            OperationLog(
                idempotency_key="rollback-me",
                operation_type=OperationType.CAMPAIGN_CREATE.value,
                payload={"probe": True},
            )
        )
        await session.flush()

        with pytest.raises(RuntimeError, match="boom"):
            await generator.athrow(RuntimeError("boom"))

        async with app.state.session_factory() as check:
            count = await check.scalar(select(func.count()).select_from(OperationLog))
        assert count == 0


async def test_lifespan_passes_timeout_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """C2: значения из настроек доходят до клиента, а не остаются дефолтами класса."""
    set_env(monkeypatch, KEITARO_TIMEOUT="7.5", KEITARO_RETRIES="3")
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            captured.update(kwargs)

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr("keitaro_wrapper.main.KeitaroClient", FakeClient)
    app = create_app()

    async with lifespan(app):
        pass

    assert captured == {"timeout": 7.5, "retries": 3}


def test_configure_logging_applies_level() -> None:
    root = logging.getLogger()
    previous_level = root.level
    try:
        configure_logging("critical")

        assert root.level == logging.CRITICAL
        assert logging.getLogger("keitaro_wrapper").level == logging.CRITICAL
    finally:
        root.setLevel(previous_level)
        logging.getLogger("keitaro_wrapper").setLevel(logging.NOTSET)
