"""Сборка FastAPI-приложения."""

from fastapi import FastAPI

from keitaro_wrapper import __version__
from keitaro_wrapper.api.router import api_router


def create_app() -> FastAPI:
    """Создаёт приложение; настройки читаются лениво, чтобы `/health` жил без `.env`."""
    app = FastAPI(
        title="Keitaro wrapper (ETOLEGKO test task)",
        version=__version__,
        description="Обёртка над Keitaro Admin API: создаватор кампаний и редактор офферов.",
    )
    app.include_router(api_router)
    return app


app = create_app()
