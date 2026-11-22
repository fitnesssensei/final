"""Служебные эндпоинты: живость процесса и готовность к работе."""

import logging
from typing import Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from keitaro_wrapper import __version__
from keitaro_wrapper.api.deps import SessionDep

logger = logging.getLogger(__name__)
router = APIRouter(tags=["service"])

# Текст для readiness: клиенту (и оркестратору) нужен следующий шаг, а не SQL драйвера
DATABASE_UNAVAILABLE = "база не отвечает: проверьте DATABASE_URL и `make migrate`"


class HealthResponse(BaseModel):
    # Literal вместо str: в OpenAPI виден единственный возможный ответ
    status: Literal["ok"] = "ok"
    version: str


class ReadyResponse(BaseModel):
    """Успешная готовность: процесс жив и БД отвечает."""

    status: Literal["ready"] = "ready"
    version: str
    database: Literal["ok"] = "ok"


@router.get("/health", response_model=HealthResponse, summary="Проверка живости приложения")
async def health() -> HealthResponse:
    """Отвечает без обращения к Keitaro и БД — годится для healthcheck в docker."""
    return HealthResponse(version=__version__)


@router.get(
    "/ready",
    response_model=ReadyResponse,
    summary="Проверка готовности: приложение и база",
    responses={503: {"description": "База недоступна или схема не накатана"}},
)
async def ready(session: SessionDep) -> ReadyResponse | JSONResponse:
    """Готовность = «можно работать»: БД отвечает на `SELECT 1`.

    Разделение с `/health` осознанное: liveness-проба Docker должна перезапускать
    зависший процесс, а не считать его мёртвым из-за выключенного Postgres.
    Readiness же честно отвечает 503, пока журнал операций писать некуда.
    Keitaro здесь не проверяется: его отказ виден на конкретной ручке (502),
    а дополнительный запрос в трекер на каждый healthcheck только создаёт шум.
    """
    try:
        await session.execute(text("SELECT 1"))
    except (OperationalError, ProgrammingError, PoolTimeoutError, ConnectionError) as exc:
        # debug, а не error: пробу дёргают каждые 30 секунд, и при осознанно
        # выключенной БД лог иначе превратился бы в поток одинаковых ошибок.
        logger.debug("readiness: база не отвечает: %s", exc)
        return JSONResponse(
            status_code=503,
            content={
                "status": "not_ready",
                "database": "unavailable",
                "detail": DATABASE_UNAVAILABLE,
            },
        )
    return ReadyResponse(version=__version__)
