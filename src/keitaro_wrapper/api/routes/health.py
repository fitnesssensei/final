"""Служебные эндпоинты."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from keitaro_wrapper import __version__

router = APIRouter(tags=["service"])


class HealthResponse(BaseModel):
    # Literal вместо str: в OpenAPI виден единственный возможный ответ
    status: Literal["ok"] = "ok"
    version: str


@router.get("/health", response_model=HealthResponse, summary="Проверка живости приложения")
async def health() -> HealthResponse:
    """Отвечает без обращения к Keitaro и БД — годится для healthcheck в docker."""
    return HealthResponse(version=__version__)
