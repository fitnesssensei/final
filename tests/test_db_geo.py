"""Тесты карты гео: ISO-2 для фильтра потока ↔ название страны в Keitaro."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.directories import resolve_keitaro_country, upsert_geo_mapping
from keitaro_wrapper.db.models import GeoMap


async def test_mapping_is_saved_and_resolved(session: AsyncSession) -> None:
    await upsert_geo_mapping(session, code="MX", keitaro_country="Mexico")
    await session.commit()

    assert await resolve_keitaro_country(session, "MX") == "Mexico"


async def test_code_is_normalized_to_uppercase(session: AsyncSession) -> None:
    """`mx` из формы и `MX` из фильтра потока — одна и та же страна."""
    mapping = await upsert_geo_mapping(session, code=" mx ", keitaro_country="Mexico")

    assert mapping.code == "MX"
    assert await resolve_keitaro_country(session, "mx") == "Mexico"


async def test_upsert_updates_existing_code(session: AsyncSession) -> None:
    await upsert_geo_mapping(session, code="PE", keitaro_country="Peru")
    await session.commit()

    await upsert_geo_mapping(session, code="PE", keitaro_country="Perú")
    await session.commit()

    assert await resolve_keitaro_country(session, "PE") == "Perú"
    assert await session.get(GeoMap, "PE") is not None


async def test_unknown_code_returns_none(session: AsyncSession) -> None:
    assert await resolve_keitaro_country(session, "ZZ") is None


@pytest.mark.parametrize("code", ["", "M", "MEX", "M1", "  "])
async def test_invalid_code_is_rejected(session: AsyncSession, code: str) -> None:
    """SQLite не проверяет `VARCHAR(2)` — длину и буквы валидирует код."""
    with pytest.raises(ValueError, match="ISO-2"):
        await upsert_geo_mapping(session, code=code, keitaro_country="Mexico")


async def test_empty_country_is_rejected(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="не может быть пустым"):
        await upsert_geo_mapping(session, code="MX", keitaro_country="   ")


async def test_country_name_is_unique(session: AsyncSession) -> None:
    """Одно название страны не может указывать на два разных ISO-кода."""
    await upsert_geo_mapping(session, code="MX", keitaro_country="Mexico")
    await session.commit()

    with pytest.raises(IntegrityError):
        await upsert_geo_mapping(session, code="ME", keitaro_country="Mexico")
        await session.commit()
