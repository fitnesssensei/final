"""Общие фикстуры тестов."""

import logging
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from keitaro_wrapper.config import Settings, get_settings
from keitaro_wrapper.db import Base, create_session_factory
from keitaro_wrapper.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-клиент поверх приложения без запуска `lifespan`.

    Настройки не читаются, поэтому фикстура не зависит от окружения. Сценарии
    со стартом приложения (fail-fast) проверяются в `tests/test_startup.py`.
    """
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


@pytest.fixture
async def db_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """Файловая SQLite на каждый тест: `:memory:` не пережил бы переподключение пула.

    Схема создаётся `create_all` — миграции проверяются отдельным тестом,
    чтобы падение Alembic не маскировало тесты репозиториев.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия поверх тестовой БД; закрывается после теста."""
    factory = create_session_factory(db_engine)
    async with factory() as db_session:
        yield db_session


@pytest.fixture(autouse=True)
def _isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отвязывает настройки от машины: убирает env vars и локальный `.env`.

    `Settings` читает и `os.environ`, и `.env` из текущего каталога. Без этой
    фикстуры прогон зависит от окружения разработчика: экспортированный
    `LOG_LEVEL=verbose` ронял тесты настроек, а чужой `DATABASE_URL` подменял
    дефолт. Имена переменных берутся из модели, поэтому новые поля настроек
    попадут сюда автоматически. Сравнение регистронезависимое — pydantic-settings
    по умолчанию не учитывает регистр в именах полей.
    """
    field_names = {name.upper() for name in Settings.model_fields}
    for name in [key for key in os.environ if key.upper() in field_names]:
        monkeypatch.delenv(name, raising=False)
    # `.env` рядом с проектом тоже не должен влиять: у тестов своё окружение
    monkeypatch.setitem(Settings.model_config, "env_file", None)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> Iterator[None]:
    """Сбрасывает кэш `get_settings()` до и после теста.

    `get_settings` кэширован через `lru_cache`, а тесты меняют окружение:
    без сброса настройки предыдущего теста (в том числе невалидные) протекали
    бы в следующий.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _restore_loggers() -> Iterator[None]:
    """Возвращает логгеры после тестов, которые запускают alembic.

    `migrations/env.py` вызывает `fileConfig` от `alembic.ini`, а тот по
    умолчанию глушит уже созданные логгеры (`disable_existing_loggers=True`):
    после `test_migrations` логи приложения перестают доходить до обработчика
    pytest, и `caplog` в следующих тестах остаётся пустым. Сбой зависел бы от
    порядка файлов, поэтому чистим после каждого теста.
    """
    yield
    logging.disable(logging.NOTSET)
    for logger in logging.Logger.manager.loggerDict.values():
        if isinstance(logger, logging.Logger):
            logger.disabled = False
