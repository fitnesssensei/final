"""Журнал операций: идемпотентность повторных запросов к Keitaro.

Сценарий шага 4: клиент присылает `Idempotency-Key`, сервис регистрирует
операцию, создаёт кампанию и записывает внешний id. Повтор с тем же ключом
(двойной клик, ретрай прокси) получает ту же строку журнала и не создаёт
вторую кампанию.

Здесь же обслуживание журнала: строки старше `OPERATIONS_RETENTION_DAYS`
удаляются на старте приложения, иначе таблица растёт бесконечно.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.models import OperationLog, OperationStatus, OperationType


@dataclass(frozen=True, slots=True)
class Claim:
    """Результат `claim_operation`.

    `created=False` значит, что ключ уже был: вызывающий код должен вернуть
    сохранённый результат (`external_id`/`status`), а не повторять POST.
    """

    operation: OperationLog
    created: bool


def _normalize_key(idempotency_key: str) -> str:
    """Проверяет ключ до записи в БД.

    В Postgres `VARCHAR(128)` обрезает длинное значение исключением, и это
    выглядело бы как 500 вместо понятной 422; в SQLite длина не проверяется
    вовсе. Поэтому ограничение дублируется в коде — одинаково для обеих СУБД.
    """
    key = idempotency_key.strip()
    if not key:
        raise ValueError("ключ идемпотентности не может быть пустым")
    if len(key) > 128:
        raise ValueError("ключ идемпотентности длиннее 128 символов")
    return key


async def find_operation(session: AsyncSession, idempotency_key: str) -> OperationLog | None:
    """Находит операцию по ключу идемпотентности (ключ нормализуется)."""
    return await session.scalar(
        select(OperationLog).where(OperationLog.idempotency_key == _normalize_key(idempotency_key))
    )


async def prune_operations(session: AsyncSession, *, older_than_days: int) -> int:
    """Удаляет записи журнала старше `older_than_days` дней и коммитит.

    Возвращает число удалённых строк. `0` дней означает «не чистить»: иначе
    настройка по умолчанию стирала бы журнал, который гарантирует
    идемпотентность повторов (клиент может повторить запрос и через месяц).

    Дата считается в UTC: `created_at` пишет сервер (`now()`), поэтому
    сравнение идёт с текущим моментом UTC, а не с локальным временем процесса.
    """
    if older_than_days <= 0:
        return 0
    cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
    result = await session.execute(delete(OperationLog).where(OperationLog.created_at < cutoff))
    await session.commit()
    # rowcount у asyncpg/aiosqlite отдаёт число строк; None трактуем как «неизвестно»
    return int(result.rowcount or 0)


async def claim_operation(
    session: AsyncSession,
    *,
    idempotency_key: str,
    operation_type: str,
    payload: dict[str, Any],
) -> Claim:
    """Регистрирует операцию или возвращает уже зарегистрированную.

    Гонка двух воркеров закрывается уникальным индексом: проигравший получает
    `IntegrityError` на `flush`, откатывает только savepoint (внешняя
    транзакция цела) и перечитывает строку победителя. Конфликт по другому
    ограничению не глотается — пробрасываем, иначе ошибка схемы выглядела бы
    как успешная идемпотентность.
    """
    existing = await find_operation(session, idempotency_key)
    if existing is not None:
        return Claim(operation=existing, created=False)

    try:
        operation_kind = OperationType(operation_type)
    except ValueError as exc:
        # Опечатка в типе иначе создала бы отдельную «операцию-призрак»,
        # которую не найдёт ни один сценарий обработки.
        raise ValueError(f"неизвестный тип операции: {operation_type!r}") from exc

    operation = OperationLog(
        idempotency_key=_normalize_key(idempotency_key),
        operation_type=operation_kind.value,
        payload=payload,
        status=OperationStatus.PENDING.value,
    )
    try:
        async with session.begin_nested():
            session.add(operation)
    except IntegrityError:
        existing = await find_operation(session, idempotency_key)
        if existing is None:
            raise
        return Claim(operation=existing, created=False)
    return Claim(operation=operation, created=True)


async def mark_succeeded(
    session: AsyncSession,
    operation: OperationLog,
    *,
    external_id: str,
    result: dict[str, Any] | None = None,
) -> OperationLog:
    """Фиксирует успех: внешний id, снимок ответа и снятый текст ошибки.

    `result` нужен идемпотентности: повтор с тем же ключом вернёт сохранённый
    ответ, не создавая вторую кампанию. `None` оставляет прежнее значение —
    так операция без результата (например, удаление) не затирает его пустотой.
    """
    operation.status = OperationStatus.SUCCEEDED.value
    operation.external_id = external_id
    operation.error = None
    if result is not None:
        operation.result = result
    await session.flush()
    return operation


async def mark_failed(
    session: AsyncSession, operation: OperationLog, *, error: str
) -> OperationLog:
    """Фиксирует ошибку, обрезая текст: стектрейсы в журнал не пишем."""
    operation.status = OperationStatus.FAILED.value
    operation.error = error[:2000]
    await session.flush()
    return operation
