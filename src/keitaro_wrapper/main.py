"""Сборка FastAPI-приложения."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import ValidationError

from keitaro_wrapper import __version__
from keitaro_wrapper.api.router import api_router
from keitaro_wrapper.config import get_settings
from keitaro_wrapper.keitaro import KeitaroClient
from keitaro_wrapper.logging_setup import configure_logging

logger = logging.getLogger(__name__)


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
    # Клиент создаётся один раз на процесс: пул соединений живёт между запросами
    client = KeitaroClient(settings.keitaro_api_root, settings.keitaro_api_key.get_secret_value())
    app.state.keitaro = client
    logger.info("Приложение запущено, Keitaro Admin API: %s", settings.keitaro_api_root)
    try:
        yield
    finally:
        # Закрываем соединения при штатной остановке и при падении старта
        await client.aclose()


def create_app() -> FastAPI:
    """Создаёт приложение; настройки проверяются в `lifespan` при старте."""
    app = FastAPI(
        title="Keitaro wrapper (ETOLEGKO test task)",
        version=__version__,
        description="Обёртка над Keitaro Admin API: создаватор кампаний и редактор офферов.",
        lifespan=lifespan,
    )
    app.include_router(api_router)
    return app


app = create_app()
