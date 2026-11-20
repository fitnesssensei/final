"""Идемпотентность сценариев записи: повтор по ключу и запись ошибок.

Создаватель и редактор используют один журнал `operation_log`: `pending`
коммитится до внешних вызовов, результат сохраняется снимком, повтор с тем же
ключом возвращает его без обращения к Keitaro.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.models import OperationLog, OperationStatus, OperationType
from keitaro_wrapper.db.operations import mark_failed
from keitaro_wrapper.services.errors import Conflict

logger = logging.getLogger(__name__)


def make_key(idempotency_key: str | None) -> str:
    """Нормализует ключ клиента или генерирует свой (`uuid4` без дефисов)."""
    return (idempotency_key or "").strip() or uuid4().hex


def replay[ResultT: BaseModel](
    operation: OperationLog,
    payload: dict[str, Any],
    result_model: type[ResultT],
    *,
    operation_type: OperationType,
) -> ResultT:
    """Возвращает сохранённый результат или объясняет, почему повтор запрещён.

    Общий для всех операций: ключ от другой операции (другое тело или другой
    тип — например, добавление вместо удаления) нельзя переиспользовать,
    незавершённую нельзя повторять, упавшую — тоже.
    """
    if operation.operation_type != operation_type.value or operation.payload != payload:
        raise Conflict(
            "Idempotency-Key уже использован с другим телом запроса — "
            "для новой операции нужен новый ключ"
        )
    if operation.status == OperationStatus.SUCCEEDED.value:
        if operation.result is None:
            raise Conflict(
                "операция уже выполнена, но результат не сохранён: проверьте её в Keitaro"
            )
        return result_model.model_validate({**operation.result, "replayed": True})
    if operation.status == OperationStatus.PENDING.value:
        raise Conflict("операция с этим Idempotency-Key ещё выполняется — повторите запрос позже")
    raise Conflict(
        f"операция с этим Idempotency-Key завершилась ошибкой: "
        f"{operation.error or 'без текста'}; исправьте причину и повторите с новым ключом"
    )


async def record_failure(session: AsyncSession, operation: OperationLog, error: str) -> None:
    """Пишет ошибку в журнал; сбой записи не должен подменять исходное исключение."""
    try:
        await mark_failed(session, operation, error=error)
        await session.commit()
    except Exception:
        logger.exception("не удалось записать ошибку операции %s", operation.idempotency_key)
        await session.rollback()
