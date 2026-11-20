"""Alembic: асинхронные миграции для Postgres и SQLite.

URL берётся из `-x db_url=...`, `sqlalchemy.url` (тесты), `DATABASE_URL`/`.env`
или дефолта compose. Полный `Settings` инстанцируется только как источник
`.env`; без `KEITARO_API_KEY` миграции всё равно работают.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from pydantic import ValidationError
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from keitaro_wrapper.config import DEFAULT_DATABASE_URL, Settings
from keitaro_wrapper.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Модели импортированы через `keitaro_wrapper.db` — metadata уже содержит таблицы
target_metadata = Base.metadata


def get_database_url() -> str:
    """Собирает URL БД, не требуя ключей Keitaro для миграций."""
    override = context.get_x_argument(as_dictionary=True).get("db_url")
    if override:
        return override
    configured = config.get_main_option("sqlalchemy.url")
    if configured:
        return configured
    try:
        return Settings().database_url
    except ValidationError:
        # Чистый клон без .env: миграциям нужен только DATABASE_URL
        return os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL


def run_migrations_offline() -> None:
    """`alembic upgrade head --sql`: печатает SQL без подключения к БД."""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # SQLite не умеет ALTER COLUMN: batch-режим пересобирает таблицу
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = get_database_url()
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
