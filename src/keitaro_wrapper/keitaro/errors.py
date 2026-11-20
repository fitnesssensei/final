"""Доменные исключения клиента Keitaro Admin API."""

from __future__ import annotations

from typing import Any


class KeitaroError(Exception):
    """Базовая ошибка Admin API.

    Хранит HTTP-статус, метод и путь запроса, а также разобранное тело ответа:
    для 422 Keitaro присылает ошибки по полям, и вызывающий код может их показать.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        method: str = "",
        path: str = "",
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.method = method
        self.path = path
        self.payload = payload


class KeitaroAuthError(KeitaroError):
    """401/403: ключ не принят или у него нет прав на операцию."""


class KeitaroNotFound(KeitaroError):
    """404: сущность не найдена (кампания, оффер, поток)."""


class KeitaroValidationError(KeitaroError):
    """400/422: Keitaro отклонил запрос, в том числе с ошибками по полям."""


class KeitaroUnavailable(KeitaroError):
    """Сеть, таймаут или 5xx после исчерпания ретраев."""
