"""Ошибки бизнес-сценариев: сервисы не знают про HTTP, но задают код ответа.

`status_code` читает обработчик в `api/errors.py`. Так сценарий остаётся
обычным Python-кодом (его удобно тестировать без FastAPI), а ручка не
переводит исключения вручную.
"""

from __future__ import annotations


class ServiceError(Exception):
    """Базовая ошибка сценария, которую надо показать клиенту."""

    status_code = 422


class RequestError(ServiceError):
    """Запрос не выполним: нет сущности, оффер неактивен и т.п. (422)."""

    status_code = 422


class Conflict(ServiceError):
    """Состояние не позволяет операцию: ключ занят, оффер уже добавлен (409)."""

    status_code = 409


class NotFound(ServiceError):
    """Запрошенная сущность не найдена в рамках сценария (404)."""

    status_code = 404
