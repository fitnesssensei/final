"""Асинхронный движок и фабрика сессий SQLAlchemy.

Движок создаётся один раз на процесс в `lifespan`; соединение с БД при старте
не открывается (`create_async_engine` ленивый), поэтому приложение поднимается
и без запущенного Postgres — миграции и БД нужны только операциям журнала.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_engine(database_url: str) -> AsyncEngine:
    """Движок с проверкой живости соединения перед выдачей из пула.

    `pool_pre_ping` спасает после перезапуска Postgres: без него первый запрос
    после рестарта падал бы с `ConnectionDoesNotExistError`.
    """
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Фабрика сессий: после `commit` объекты остаются пригодными для чтения.

    `expire_on_commit=False` важен для FastAPI: ответ сериализуется уже после
    выхода из зависимости, и «протухшие» атрибуты дали бы ленивую догрузку
    в закрытой сессии.
    """
    return async_sessionmaker(engine, expire_on_commit=False)
