"""Сборка FastAPI-приложения."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from keitaro_wrapper import __version__
from keitaro_wrapper.api.errors import register_exception_handlers
from keitaro_wrapper.api.router import api_router
from keitaro_wrapper.config import get_settings
from keitaro_wrapper.db import create_engine, create_session_factory
from keitaro_wrapper.db.operations import prune_operations
from keitaro_wrapper.keitaro import KeitaroClient
from keitaro_wrapper.logging_setup import configure_logging
from keitaro_wrapper.services import reference

logger = logging.getLogger(__name__)


async def prune_old_operations(
    session_factory: async_sessionmaker[AsyncSession], *, days: int
) -> None:
    """best-effort чистка журнала на старте: при недоступной БД старт не валим.

    Журнал — вспомогательная структура: приложение умеет работать и с 503 на
    ручках журнала, поэтому сбой чистки превращается в предупреждение в логе,
    а не в падение сервиса. Ошибки сценариев (не-БД) пробрасываются: иначе
    опечатка в коде удаления осталась бы незамеченной.
    """
    if days <= 0:
        return
    try:
        async with session_factory() as session:
            removed = await prune_operations(session, older_than_days=days)
    except SQLAlchemyError as exc:
        logger.warning("Журнал операций: чистка пропущена (%s)", exc)
        return
    if removed:
        logger.info("Журнал операций: удалено %s записей старше %s дн.", removed, days)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Читает настройки на старте: с неверным `.env` приложение не поднимается.

    Без этого `/health` отвечал бы 200 при пустом ключе или `http://` в адресе,
    и сломанный конфиг всплывал бы только на первом запросе к Keitaro.
    """
    try:
        settings = get_settings()
    except ValidationError as exc:
        # Только имена полей и тексты ошибок: значения в логи не попадают
        problems = "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in exc.errors() if err["loc"])
        raise RuntimeError(
            "Настройки не прошли валидацию — проверьте .env (шаблон в .env.example): " + problems
        ) from exc

    configure_logging(settings.log_level)
    # Настройки кладём в состояние приложения: зависимость авторизации, TTL
    # справочников и ретенция журнала читают их после старта, не перечитывая
    # окружение на каждый запрос.
    app.state.settings = settings
    # Клиент создаётся один раз на процесс: пул соединений живёт между запросами
    client = KeitaroClient(
        settings.keitaro_api_root,
        settings.keitaro_api_key.get_secret_value(),
        timeout=settings.keitaro_timeout,
        retries=settings.keitaro_retries,
    )
    app.state.keitaro = client
    # Движок ленивый: соединение с БД откроется на первом запросе к журналу,
    # поэтому `/health` и чтение Keitaro работают и без поднятого Postgres.
    # Если движок не создался (кривой DATABASE_URL), клиент закрываем здесь же:
    # иначе httpx-пул провисит до выхода процесса.
    try:
        engine = create_engine(settings.database_url)
    except Exception:
        await client.aclose()
        raise
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)
    # TTL справочников и чистка журнала — свойства процесса: настраиваются один
    # раз на старте, чтобы ручки не читали окружение на каждый запрос.
    reference.configure_cache_ttl(settings.reference_cache_ttl)
    await prune_old_operations(app.state.session_factory, days=settings.operations_retention_days)
    logger.info(
        "Приложение запущено, Keitaro Admin API: %s, БД: %s",
        settings.keitaro_api_root,
        engine.url.get_backend_name(),
    )
    try:
        yield
    finally:
        # Закрываем соединения при штатной остановке и при падении старта
        await client.aclose()
        await engine.dispose()


def create_app() -> FastAPI:
    """Создаёт приложение; настройки проверяются в `lifespan` при старте."""
    app = FastAPI(
        title="Keitaro wrapper (ETOLEGKO test task)",
        version=__version__,
        description="Обёртка над Keitaro Admin API: создаватор кампаний и редактор офферов.",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    register_exception_handlers(app)
    return app


app = create_app()
