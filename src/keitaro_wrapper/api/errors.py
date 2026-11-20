"""Обработчики ошибок уровня API.

Ошибки БД не должны выглядеть как 500 «что-то сломалось»: чаще всего это
ненакатанные миграции (`UndefinedTable`) или недоступный Postgres. Для клиента
это временное состояние (503) с подсказкой, что делать, а не баг приложения.

Ошибки Keitaro тоже не 500: 401/403 ключа — проблема нашей конфигурации (502),
404 — сущность не найдена, 400/422 — невалидные данные (422), сеть и 5xx —
502. Текст ответа трекера в лог, клиенту — понятное объяснение без ключа.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from keitaro_wrapper.keitaro.errors import (
    KeitaroAuthError,
    KeitaroError,
    KeitaroNotFound,
    KeitaroUnavailable,
    KeitaroValidationError,
)
from keitaro_wrapper.services.errors import ServiceError

logger = logging.getLogger(__name__)

DATABASE_NOT_READY = (
    "Локальная база не готова: выполните `make migrate` (alembic upgrade head) "
    "и проверьте DATABASE_URL"
)
KEITARO_UNAVAILABLE = "Keitaro недоступен — повторите запрос позже"
KEITARO_AUTH = "Keitaro отклонил ключ API — проверьте KEITARO_API_KEY и права ключа"
KEITARO_UNEXPECTED = "Неожиданный ответ Keitaro — подробности в логе приложения"


async def handle_database_not_ready(request: Request, exc: Exception) -> JSONResponse:
    """503 с подсказкой про миграции; SQL и параметры остаются в логе.

    В ответ не попадает текст драйвера: там бывают имена таблиц и значения
    параметров, а клиенту нужен только следующий шаг.
    """
    logger.error("БД не готова (%s %s): %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=503, content={"detail": DATABASE_NOT_READY})


def _keitaro_status(exc: KeitaroError) -> int:
    """HTTP-статус для ошибки трекера.

    `KeitaroAuthError` — это 502, а не 401: ключ принадлежит сервису, клиент
    не может его исправить, и 401 увёл бы его к собственному логину.
    """
    if isinstance(exc, (KeitaroAuthError, KeitaroUnavailable)):
        return 502
    if isinstance(exc, KeitaroNotFound):
        return 404
    if isinstance(exc, KeitaroValidationError):
        return 422
    return 502


def _keitaro_detail(exc: KeitaroError) -> str:
    """Текст для клиента: без URL с query-параметрами и без тела ответа трекера."""
    if isinstance(exc, KeitaroAuthError):
        return KEITARO_AUTH
    if isinstance(exc, KeitaroUnavailable):
        return KEITARO_UNAVAILABLE
    if isinstance(exc, KeitaroNotFound):
        return f"в Keitaro не найдено: {exc.message}"
    if isinstance(exc, KeitaroValidationError):
        return exc.message
    return KEITARO_UNEXPECTED


async def handle_keitaro_error(request: Request, exc: Exception) -> JSONResponse:
    """Превращает ошибку трекера в 4xx/502 с понятным текстом.

    Полный текст (метод, путь, ответ Keitaro) остаётся в логе: там бывают
    детали контракта, которые клиенту не нужны.
    """
    logger.warning("Keitaro (%s %s): %s", request.method, request.url.path, exc)
    assert isinstance(exc, KeitaroError)  # обработчик зарегистрирован на этот класс
    return JSONResponse(status_code=_keitaro_status(exc), content={"detail": _keitaro_detail(exc)})


async def handle_service_error(request: Request, exc: Exception) -> JSONResponse:
    """Ошибки сценариев: 422 «не выполнимо», 409 «состояние не позволяет», 404."""
    assert isinstance(exc, ServiceError)  # обработчик зарегистрирован на этот класс
    logger.info("Сценарий (%s %s): %s", request.method, request.url.path, exc)
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


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
    # Starlette ищет обработчик по MRO, поэтому базовых классов достаточно
    app.add_exception_handler(KeitaroError, handle_keitaro_error)
    app.add_exception_handler(ServiceError, handle_service_error)
