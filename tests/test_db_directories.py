"""Тесты кэша справочников: замена целиком, сортировка, изоляция видов."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.directories import (
    DirectoryEntry,
    list_directory,
    replace_directory,
)


async def test_replace_directory_creates_entries(session: AsyncSession) -> None:
    entries = [
        DirectoryEntry(external_id="2", name="Beta", payload={"id": 2}),
        DirectoryEntry(external_id="1", name="Alpha", payload={"id": 1}),
    ]

    written = await replace_directory(session, kind="offers", entries=entries)
    await session.commit()

    rows = await list_directory(session, "offers")
    assert written == 2
    # Селекты показывают справочник по алфавиту, а не в порядке ответа API
    assert [(row.external_id, row.name) for row in rows] == [("1", "Alpha"), ("2", "Beta")]
    assert rows[0].payload == {"id": 1}


async def test_replace_directory_removes_stale_entries(session: AsyncSession) -> None:
    """Оффер, удалённый в Keitaro, не должен остаться в кэше."""
    await replace_directory(
        session,
        kind="offers",
        entries=[
            DirectoryEntry(external_id="1", name="Alpha", payload={"id": 1}),
            DirectoryEntry(external_id="2", name="Beta", payload={"id": 2}),
        ],
    )
    await session.commit()

    written = await replace_directory(
        session,
        kind="offers",
        entries=[DirectoryEntry(external_id="1", name="Alpha (new)", payload={"id": 1})],
    )
    await session.commit()

    rows = await list_directory(session, "offers")
    assert written == 1
    assert [(row.external_id, row.name) for row in rows] == [("1", "Alpha (new)")]


async def test_empty_directory_clears_cache(session: AsyncSession) -> None:
    """Пустой ответ Keitaro — это «данных нет», а не «оставить как было»."""
    await replace_directory(
        session,
        kind="domains",
        entries=[DirectoryEntry(external_id="1", name="example.com", payload={"id": 1})],
    )
    await session.commit()

    assert await replace_directory(session, kind="domains", entries=[]) == 0
    await session.commit()

    assert await list_directory(session, "domains") == []


async def test_directory_kinds_are_isolated(session: AsyncSession) -> None:
    await replace_directory(
        session,
        kind="offers",
        entries=[DirectoryEntry(external_id="1", name="Offer", payload={"id": 1})],
    )
    await replace_directory(
        session,
        kind="domains",
        entries=[DirectoryEntry(external_id="1", name="Domain", payload={"id": 1})],
    )
    await session.commit()

    offers = await list_directory(session, "offers")
    domains = await list_directory(session, "domains")
    assert [row.name for row in offers] == ["Offer"]
    assert [row.name for row in domains] == ["Domain"]


async def test_large_stale_set_does_not_hit_parameter_limit(session: AsyncSession) -> None:
    """Замена 1100 записей одной: SQLite не любит `IN` с >999 параметрами."""
    entries = [
        DirectoryEntry(external_id=str(number), name=f"Offer {number}", payload={})
        for number in range(1100)
    ]
    await replace_directory(session, kind="offers", entries=entries)
    await session.commit()

    written = await replace_directory(
        session,
        kind="offers",
        entries=[DirectoryEntry(external_id="5000", name="Only", payload={})],
    )
    await session.commit()

    rows = await list_directory(session, "offers")
    assert written == 1
    assert [(row.external_id, row.name) for row in rows] == [("5000", "Only")]


async def test_duplicate_batch_does_not_write_partial_rows(session: AsyncSession) -> None:
    """Дубли проверяются до записи: после ValueError в сессии нет полузаписей."""
    entries = [
        DirectoryEntry(external_id="1", name="First", payload={}),
        DirectoryEntry(external_id="1", name="Second", payload={}),
    ]

    with pytest.raises(ValueError, match="дубль external_id"):
        await replace_directory(session, kind="offers", entries=entries)

    assert await list_directory(session, "offers") == []


async def test_unknown_kind_is_rejected(session: AsyncSession) -> None:
    """Опечатка в `kind` не должна молча создать новую таблицу-свалку."""
    with pytest.raises(ValueError, match="неизвестный справочник"):
        await replace_directory(session, kind="offerz", entries=[])
