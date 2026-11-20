"""Обработчики ошибок уровня API.

Ошибки БД не должны выглядеть как 500 «что-то сломалось»: чаще всего это
ненакатанные миграции (`UndefinedTable`) или недоступный Postgres. Для клиента
это временное состояние (503) с подсказкой, что делать, а не баг приложения.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

logger = logging.getLogger(__name__)

DATABASE_NOT_READY = (
    "Локальная база не готова: выполните `make migrate` (alembic upgrade head) "
    "и проверьте DATABASE_URL"
)


async def handle_database_not_ready(request: Request, exc: Exception) -> JSONResponse:
    """503 с подсказкой про миграции; SQL и параметры остаются в логе.

    В ответ не попадает текст драйвера: там бывают имена таблиц и значения
    параметров, а клиенту нужен только следующий шаг.
    """
    logger.error("БД не готова (%s %s): %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": DATABASE_NOT_READY})


def register_exception_handlers(app: FastAPI) -> None:
    """Подключает обработчики к приложению (вызывается из `create_app`).

    `ConnectionError` — не перестраховка: при недоступном Postgres asyncpg
    поднимает `ConnectionRefusedError` напрямую, не оборачивая его в
    `OperationalError` (проверено на живом отказе соединения).
    `PoolTimeoutError` — пул не выдал соединение за `pool_timeout`.
    """
    app.add_exception_handler(OperationalError, handle_database_not_ready)
    app.add_exception_handler(ProgrammingError, handle_database_not_ready)
    app.add_exception_handler(PoolTimeoutError, handle_database_not_ready)
    app.add_exception_handler(ConnectionError, handle_database_not_ready)
