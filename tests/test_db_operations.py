"""Тесты журнала операций: повтор с тем же ключом не создаёт дубль."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from keitaro_wrapper.db import create_session_factory
from keitaro_wrapper.db.models import OperationLog, OperationStatus, OperationType
from keitaro_wrapper.db.operations import (
    claim_operation,
    find_operation,
    mark_failed,
    mark_succeeded,
)

PAYLOAD = {"name": "MX test", "geo": "MX"}


async def test_repeated_claim_returns_same_operation(session: AsyncSession) -> None:
    """Двойной клик или ретрай прокси не должен создавать вторую операцию."""
    first = await claim_operation(
        session,
        idempotency_key="key-1",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )
    await session.commit()

    second = await claim_operation(
        session,
        idempotency_key="key-1",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )

    assert first.created is True
    assert second.created is False
    assert second.operation.id == first.operation.id
    assert await session.scalar(select(func.count()).select_from(OperationLog)) == 1


async def test_different_keys_create_separate_operations(session: AsyncSession) -> None:
    first = await claim_operation(
        session,
        idempotency_key="key-1",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )
    second = await claim_operation(
        session,
        idempotency_key="key-2",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )
    await session.commit()

    assert first.created and second.created
    assert first.operation.id != second.operation.id
    assert await session.scalar(select(func.count()).select_from(OperationLog)) == 2


async def test_new_operation_starts_as_pending(session: AsyncSession) -> None:
    claim = await claim_operation(
        session,
        idempotency_key="key-pending",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )

    assert claim.operation.status == OperationStatus.PENDING
    assert claim.operation.external_id is None
    assert claim.operation.error is None


async def test_mark_succeeded_stores_external_id(session: AsyncSession) -> None:
    claim = await claim_operation(
        session,
        idempotency_key="key-ok",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )
    await mark_failed(session, claim.operation, error="Keitaro 422: alias занят")

    await mark_succeeded(session, claim.operation, external_id="42")
    await session.commit()

    stored = await find_operation(session, "key-ok")
    assert stored is not None
    assert stored.status == OperationStatus.SUCCEEDED
    assert stored.external_id == "42"
    # Успех снимает текст прошлой ошибки: иначе журнал противоречит сам себе
    assert stored.error is None


async def test_mark_failed_truncates_long_error(session: AsyncSession) -> None:
    """Стектрейс в журнал не пишем: колонка ограничена 2000 символами."""
    claim = await claim_operation(
        session,
        idempotency_key="key-fail",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )

    await mark_failed(session, claim.operation, error="x" * 5000)
    await session.commit()

    stored = await find_operation(session, "key-fail")
    assert stored is not None
    assert stored.status == OperationStatus.FAILED
    assert stored.error is not None
    assert len(stored.error) == 2000


async def test_key_is_normalized(session: AsyncSession) -> None:
    """Ключ с пробелами по краям — тот же ключ, а не дубль."""
    first = await claim_operation(
        session,
        idempotency_key="  key-spaces  ",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )
    await session.commit()

    second = await claim_operation(
        session,
        idempotency_key="key-spaces",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )

    assert first.created is True
    assert second.created is False
    assert second.operation.id == first.operation.id
    assert second.operation.idempotency_key == "key-spaces"


@pytest.mark.parametrize("key", ["", "   ", "k" * 129])
async def test_invalid_key_is_rejected_before_insert(session: AsyncSession, key: str) -> None:
    """Пустой/длинный ключ — понятная ошибка, а не 500 от VARCHAR в Postgres."""
    with pytest.raises(ValueError, match="ключ идемпотентности"):
        await claim_operation(
            session,
            idempotency_key=key,
            operation_type=OperationType.CAMPAIGN_CREATE,
            payload=PAYLOAD,
        )

    assert await session.scalar(select(func.count()).select_from(OperationLog)) == 0


async def test_unknown_operation_type_is_rejected(session: AsyncSession) -> None:
    """Опечатка в типе операции не должна создавать «операцию-призрак»."""
    with pytest.raises(ValueError, match="неизвестный тип операции"):
        await claim_operation(
            session,
            idempotency_key="key-type",
            operation_type="campaign_creat",
            payload=PAYLOAD,
        )


async def test_duplicate_key_is_rejected_by_database(
    session: AsyncSession, db_engine: AsyncEngine
) -> None:
    """Уникальность держит БД, а не только предварительный SELECT.

    Гонка двух воркеров: оба прошли `find_operation`, но вставить строку
    сможет только один — второй получит IntegrityError и перечитает запись.
    """
    await claim_operation(
        session,
        idempotency_key="dup",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=PAYLOAD,
    )
    await session.commit()

    factory = create_session_factory(db_engine)
    async with factory() as other:
        other.add(
            OperationLog(
                idempotency_key="dup",
                operation_type=OperationType.CAMPAIGN_CREATE.value,
                payload=PAYLOAD,
            )
        )
        with pytest.raises(IntegrityError):
            await other.commit()
