"""Справочники для селектов UI: читаются из локального кэша.

Создаватель и редактор не должны дёргать Keitaro на каждый рендер: домены,
группы, источники, офферы и кампании уже лежат в `dictionary_cache`. Если кэш
пуст (первый запуск), справочники читаются из Keitaro один раз и сохраняются.

TTL включается из `lifespan` (`REFERENCE_CACHE_TTL`): без него новые офферы и
кампании появлялись бы в селектах только после перезапуска или кнопки
«обновить». Настройка читается один раз на старте — сервисный слой не ходит в
окружение сам, поэтому тесты не зависят от `.env` машины.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.directories import (
    DIRECTORY_KINDS,
    entries_from_models,
    list_directory,
    replace_directory,
)
from keitaro_wrapper.db.models import DictionaryCache
from keitaro_wrapper.keitaro import KeitaroClient

# 0 — TTL выключен: кэш обновляется только когда пуст (поведение до появления TTL).
# В проде значение приходит из `REFERENCE_CACHE_TTL` (по умолчанию 900 секунд).
_cache_ttl: float = 0.0
# Когда справочник последний раз читался из Keitaro (по часам процесса).
_refreshed_at: dict[str, float] = {}
# Монотонные часы: тесты подменяют их, чтобы не ждать реальные минуты.
_clock: Callable[[], float] = time.monotonic


def configure_cache_ttl(seconds: float) -> None:
    """Включает TTL справочников; `0` — выключить (обновлять только пустой кэш).

    Вызывается из `lifespan` после чтения настроек. Отдельная функция, а не
    параметр каждой ручки: TTL — свойство процесса, а не отдельного запроса.
    """
    global _cache_ttl
    _cache_ttl = max(0.0, float(seconds))


def reset_cache_state() -> None:
    """Сбрасывает отметки времени обновления (нужно тестам между прогонами)."""
    _refreshed_at.clear()


def _is_stale(kind: str) -> bool:
    """Протух ли кэш справочника.

    TTL выключен → кэш считается свежим (прежнее поведение). TTL включён, но
    справочник в этом процессе ещё не обновляли → протух: после рестарта кэш
    в БД может быть сколь угодно старым, а отметок времени у нас нет.
    """
    if _cache_ttl <= 0:
        return False
    last = _refreshed_at.get(kind)
    return last is None or _clock() - last >= _cache_ttl


class DirectoryOption(BaseModel):
    """Вариант селекта: id, имя и подсказка состояния/страны."""

    id: int
    name: str
    state: str | None = None
    country: list[str] = Field(default_factory=list)


class CreatorOptions(BaseModel):
    """Все селекты экрана «Создатель»."""

    domains: list[DirectoryOption] = Field(default_factory=list)
    groups: list[DirectoryOption] = Field(default_factory=list)
    traffic_sources: list[DirectoryOption] = Field(default_factory=list)
    offers: list[DirectoryOption] = Field(default_factory=list)


async def fetch_directory(client: KeitaroClient, kind: str) -> list[Any]:
    """Читает справочник из Keitaro (без записи в БД).

    Для групп важен `type=campaigns`: без него живой инстанс отвечает 400,
    а группы офферов в селекте создавателя не нужны.
    """
    if kind == "domains":
        return await client.list_domains()
    if kind == "groups":
        return await client.list_groups("campaigns")
    if kind == "traffic_sources":
        return await client.list_traffic_sources()
    if kind == "campaigns":
        return await client.list_campaigns()
    if kind == "offers":
        return await client.list_offers()
    raise ValueError(f"неизвестный справочник: {kind!r}")


async def refresh_directory(session: AsyncSession, client: KeitaroClient, kind: str) -> int:
    """Читает справочник и полностью заменяет его локальный кэш.

    Отметка времени обновления ставится здесь же: и плановое обновление по TTL,
    и явное `?refresh=1` должны сдвигать срок свежести одинаково.
    """
    if kind not in DIRECTORY_KINDS:
        raise ValueError(f"неизвестный справочник: {kind!r}")
    items = await fetch_directory(client, kind)
    await replace_directory(session, kind=kind, entries=entries_from_models(items))
    _refreshed_at[kind] = _clock()
    return len(items)


async def ensure_cached(
    session: AsyncSession, client: KeitaroClient, *kinds: str
) -> dict[str, list[DictionaryCache]]:
    """Возвращает записи справочников; пустые и протухшие обновляет из Keitaro.

    Сетевые вызовы для разных справочников идут параллельно, а запись в БД —
    последовательно: одна `AsyncSession` не допускает конкурентных операций.
    """
    rows = {kind: await list_directory(session, kind) for kind in kinds}
    missing = [kind for kind in kinds if not rows[kind] or _is_stale(kind)]
    if not missing:
        return rows

    fetched = await asyncio.gather(*(fetch_directory(client, kind) for kind in missing))
    for kind, items in zip(missing, fetched, strict=True):
        await replace_directory(session, kind=kind, entries=entries_from_models(items))
        # Отметка ставится и здесь: обновление идёт мимо `refresh_directory`
        # (сетевые вызовы параллельные), но срок свежести должен сдвигаться.
        _refreshed_at[kind] = _clock()
    await session.commit()
    return {kind: await list_directory(session, kind) for kind in kinds}


def _option(row: DictionaryCache) -> DirectoryOption:
    """Превращает запись кэша в вариант селекта, сохраняя state/country."""
    payload = row.payload if isinstance(row.payload, dict) else {}
    countries = payload.get("country")
    return DirectoryOption(
        id=int(row.external_id),
        name=row.name,
        state=payload.get("state"),
        country=[str(item) for item in countries] if isinstance(countries, list) else [],
    )


async def creator_options(session: AsyncSession, client: KeitaroClient) -> CreatorOptions:
    """Селекты создавателя: домен, группа, источник, активные офферы."""
    rows = await ensure_cached(session, client, "domains", "groups", "traffic_sources", "offers")
    return CreatorOptions(
        domains=[_option(row) for row in rows["domains"]],
        groups=[_option(row) for row in rows["groups"]],
        traffic_sources=[_option(row) for row in rows["traffic_sources"]],
        offers=[_option(row) for row in rows["offers"]],
    )


async def campaign_options(
    session: AsyncSession, client: KeitaroClient, *, refresh: bool = False
) -> list[DirectoryOption]:
    """Кампании для селекта редактора; `refresh` — принудительное чтение Keitaro."""
    if refresh:
        await refresh_directory(session, client, "campaigns")
        await session.commit()
    rows = await ensure_cached(session, client, "campaigns")
    return [_option(row) for row in rows["campaigns"]]


async def offer_options(
    session: AsyncSession, client: KeitaroClient, *, refresh: bool = False
) -> list[DirectoryOption]:
    """Офферы для добавления в поток (из кэша, при пустом — из Keitaro)."""
    if refresh:
        await refresh_directory(session, client, "offers")
        await session.commit()
    rows = await ensure_cached(session, client, "offers")
    return [_option(row) for row in rows["offers"]]
