"""Роутеры HTTP-слоя."""

from fastapi import APIRouter, Depends

from keitaro_wrapper.api.deps import require_auth
from keitaro_wrapper.api.routes import campaigns, health, ui

api_router = APIRouter()
# Служебные ручки открыты всегда: их дёргают Docker и оркестратор, у которых
# нет учётных данных, а в ответе нет ничего секретного.
api_router.include_router(health.router)
# Всё, что меняет данные или показывает кампании, защищено Basic-авторизацией,
# если она настроена (AUTH_USER/AUTH_PASSWORD). Без настройки — открыто.
api_router.include_router(campaigns.router, dependencies=[Depends(require_auth)])
api_router.include_router(ui.root_router, dependencies=[Depends(require_auth)])
api_router.include_router(ui.router, dependencies=[Depends(require_auth)])
