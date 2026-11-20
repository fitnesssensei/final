"""Роутеры HTTP-слоя."""

from fastapi import APIRouter

from keitaro_wrapper.api.routes import campaigns, health

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(campaigns.router)
