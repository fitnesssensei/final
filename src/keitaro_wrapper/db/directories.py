"""Кэш справочников Keitaro и карта гео-кодов.

Справочники читаются из Keitaro редко (офферы/домены меняются не каждый день),
поэтому UI и создаватель берут их из локальной таблицы, а обновление делает
явный вызов `replace_directory` после успешного `list_*` клиента.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.models import DictionaryCache, GeoMap

# Значения `kind` совпадают с именами справочников Keitaro
DIRECTORY_KINDS = frozenset({"campaigns", "offers", "domains", "groups", "traffic_sources"})


@dataclass(frozen=True, slots=True)
class DirectoryEntry:
    """Одна запись справочника: внешний id, имя и исходный JSON."""

    external_id: str
    name: str
    payload: dict[str, Any]


async def replace_directory(
    session: AsyncSession, *, kind: str, entries: Sequence[DirectoryEntry]
) -> int:
    """Полностью заменяет кэш справочника и возвращает число записей.

    Именно замена, а не upsert по одной: удалённые в Keitaro офферы не должны
    висеть в селектах. Если Keitaro вернул пустой список, кэш очищается —
    «пусто» здесь означает «в трекере действительно нет данных».
    """
    if kind not in DIRECTORY_KINDS:
        raise ValueError(f"неизвестный справочник: {kind!r}")

    # Дубли проверяем до записи: иначе ValueError оставил бы в сессии
    # наполовину применённый набор, если вызывающий код его перехватит.
    seen: set[str] = set()
    for entry in entries:
        if entry.external_id in seen:
            raise ValueError(f"дубль external_id {entry.external_id!r} в справочнике {kind!r}")
        seen.add(entry.external_id)

    existing = {
        row.external_id: row
        for row in await session.scalars(
            select(DictionaryCache).where(DictionaryCache.kind == kind)
        )
    }
    for entry in entries:
        row = existing.get(entry.external_id)
        if row is None:
            session.add(
                DictionaryCache(
                    kind=kind,
                    external_id=entry.external_id,
                    name=entry.name,
                    payload=entry.payload,
                )
            )
        else:
            row.name = entry.name
            row.payload = entry.payload

    # Удаляем пропавшие одним DELETE по external_id, а не по списку id:
    # в SQLite лимит 999 параметров, и большой справочник упал бы на нём.
    stale_filter = DictionaryCache.kind == kind
    if seen:
        stale_filter = stale_filter & DictionaryCache.external_id.not_in(seen)
    await session.execute(delete(DictionaryCache).where(stale_filter))
    await session.flush()
    return len(entries)


async def list_directory(session: AsyncSession, kind: str) -> list[DictionaryCache]:
    """Записи справочника по алфавиту — в таком порядке их показывают селекты."""
    rows = await session.scalars(
        select(DictionaryCache)
        .where(DictionaryCache.kind == kind)
        .order_by(DictionaryCache.name, DictionaryCache.external_id)
    )
    return list(rows)


async def upsert_geo_mapping(session: AsyncSession, *, code: str, keitaro_country: str) -> GeoMap:
    """Сохраняет соответствие ISO-2 → название страны в Keitaro.

    Код нормализуется в верхний регистр: `mx` из UI и `MX` из фильтра потока —
    одна и та же страна. Длина проверяется здесь, потому что SQLite не
    ограничивает `VARCHAR(2)`.
    """
    normalized = code.strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        raise ValueError(f"гео-код должен быть ISO-2 (две буквы), получено {code!r}")
    country = keitaro_country.strip()
    if not country:
        raise ValueError("название страны в Keitaro не может быть пустым")

    mapping = await session.get(GeoMap, normalized)
    if mapping is None:
        mapping = GeoMap(code=normalized, keitaro_country=country)
        session.add(mapping)
    else:
        mapping.keitaro_country = country
    await session.flush()
    return mapping


async def resolve_keitaro_country(session: AsyncSession, code: str) -> str | None:
    """Возвращает название страны для фильтра потока или None, если пары нет."""
    mapping = await session.get(GeoMap, code.strip().upper())
    return mapping.keitaro_country if mapping is not None else None
