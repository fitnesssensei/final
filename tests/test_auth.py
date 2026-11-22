"""Тесты необязательной HTTP Basic-авторизации.

Авторизация включается парой `AUTH_USER`/`AUTH_PASSWORD` и защищает `/api/*`
и `/ui/*`. Служебные ручки (`/health`, `/ready`) остаются открытыми: их дёргают
Docker и оркестратор без учётных данных.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from keitaro_wrapper.api.deps import AUTH_REALM
from keitaro_wrapper.config import Settings
from keitaro_wrapper.db import Base, create_session_factory
from keitaro_wrapper.main import create_app
from tests.stub_keitaro import StubKeitaro

BASE_KWARGS = {
    "keitaro_base_url": "https://tlgk.host",
    "keitaro_api_key": "test-key",
    "keitaro_domain_id": 1813,
    "keitaro_group_id": 10332,
    "keitaro_source_id": 836,
}


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**BASE_KWARGS, **overrides})


@asynccontextmanager
async def app_with_auth(
    engine: AsyncEngine, settings: Settings
) -> AsyncIterator[httpx.AsyncClient]:
    """Приложение с настройками и живой SQLite, но без `lifespan`.

    Настройки обычно кладёт `lifespan`; здесь они подставляются напрямую, чтобы
    тест не зависел от `.env` и от переменных окружения машины.
    """
    app = create_app()
    app.state.settings = settings
    app.state.keitaro = StubKeitaro()
    app.state.session_factory = create_session_factory(engine)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Файловая SQLite со схемой: ручкам нужна рабочая сессия."""
    db = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'auth.db'}")
    async with db.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield db
    await db.dispose()


async def test_api_requires_credentials_when_auth_is_on(engine: AsyncEngine) -> None:
    """Без заголовка Authorization — 401 с приглашением Basic (браузер покажет окно)."""
    settings = make_settings(auth_user="admin", auth_password="secret")

    async with app_with_auth(engine, settings) as client:
        response = await client.get("/api/campaigns")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == AUTH_REALM


async def test_api_rejects_wrong_password(engine: AsyncEngine) -> None:
    """Верный логин с неверным паролем — тоже 401, а не «пустим, раз логин есть»."""
    settings = make_settings(auth_user="admin", auth_password="secret")

    async with app_with_auth(engine, settings) as client:
        response = await client.get("/api/campaigns", auth=("admin", "wrong"))

    assert response.status_code == 401


async def test_api_accepts_correct_credentials(engine: AsyncEngine) -> None:
    """С верной парой ручка работает как обычно (двойник Keitaro + SQLite)."""
    settings = make_settings(auth_user="admin", auth_password="secret")

    async with app_with_auth(engine, settings) as client:
        response = await client.get("/api/campaigns", auth=("admin", "secret"))

    assert response.status_code == 200
    assert response.json()


async def test_ui_page_requires_credentials(engine: AsyncEngine) -> None:
    """HTML-страницы защищены так же: иначе интерфейс обходил бы авторизацию API."""
    settings = make_settings(auth_user="admin", auth_password="secret")

    async with app_with_auth(engine, settings) as client:
        response = await client.get("/ui/creator")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == AUTH_REALM


async def test_service_endpoints_stay_open(engine: AsyncEngine) -> None:
    """Liveness и readiness открыты: их дёргают Docker и оркестратор без пароля."""
    settings = make_settings(auth_user="admin", auth_password="secret")

    async with app_with_auth(engine, settings) as client:
        assert (await client.get("/health")).status_code == 200
        assert (await client.get("/ready")).status_code == 200


async def test_auth_is_off_by_default(engine: AsyncEngine) -> None:
    """Без пары логин/пароль ручки открыты — прежнее поведение не меняется."""
    settings = make_settings()

    async with app_with_auth(engine, settings) as client:
        response = await client.get("/api/campaigns")

    assert response.status_code == 200


def test_half_of_auth_pair_is_a_startup_error() -> None:
    """Только логин или только пароль — ошибка настроек, а не «почти включено»."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(auth_user="admin")

    assert "AUTH_USER и AUTH_PASSWORD" in str(exc_info.value)


def test_empty_auth_values_mean_disabled() -> None:
    """`AUTH_USER=`/`AUTH_PASSWORD=` из `.env` — это «выключено», а не пустая пара."""
    settings = make_settings(auth_user="", auth_password="   ")

    assert settings.auth_user is None
    assert settings.auth_password is None
