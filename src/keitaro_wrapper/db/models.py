"""Локальные таблицы: журнал операций, кэш справочников и карта гео.

Источник истины — Keitaro; локальная БД нужна для идемпотентности повторных
запросов (журнал) и для быстрых селектов без похода в трекер (кэш).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from keitaro_wrapper.db.base import Base


class OperationStatus(StrEnum):
    """Состояние операции в журнале.

    `pending` — запись создана, Keitaro ещё не ответил; после ответа строка
    переходит в `succeeded` (с внешним id) или `failed` (с текстом ошибки).
    """

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class OperationType(StrEnum):
    """Тип операции. Хранится строкой: новый тип не требует миграции."""

    CAMPAIGN_CREATE = "campaign_create"
    OFFER_ADD = "offer_add"
    OFFER_REMOVE = "offer_remove"


class OperationLog(Base):
    """Журнал операций: один `idempotency_key` — одна операция.

    Уникальный индекс на ключ закрывает гонку воркеров: повторный запрос
    с тем же ключом получит существующую строку, а не создаст дубль.
    """

    __tablename__ = "operation_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed')",
            name="status_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True)
    operation_type: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    external_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(
        String(16),
        default=OperationStatus.PENDING.value,
        server_default=OperationStatus.PENDING.value,
    )
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DictionaryCache(Base):
    """Кэш справочника Keitaro: кампании, офферы, домены, группы, источники.

    `payload` хранит исходный JSON целиком, чтобы шаг 4 не зависел от набора
    колонок; `name` вынесен отдельно для селектов и сортировки.
    """

    __tablename__ = "dictionary_cache"
    __table_args__ = (UniqueConstraint("kind", "external_id", name="kind_external_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    refreshed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class GeoMap(Base):
    """Соответствие ISO-2 (для фильтра потока) названию страны в Keitaro.

    Живой инстанс отдаёт `Offer.country = ['Peru']`, а фильтр потока ждёт `MX`,
    поэтому оба представления хранятся рядом.
    """

    __tablename__ = "geo_map"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    keitaro_country: Mapped[str] = mapped_column(String(64), unique=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
