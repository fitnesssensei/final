"""Редактор кампании: список, потоки с офферами, веса и цифры из отчёта.

Правила редактора:

- веса офферов в потоке всегда в сумме 100; после добавления/удаления доли
  пересчитываются поровну (34/33/33 для трёх), остаток отдаётся первым;
- позиции офферов — порядок в потоке, 1..N;
- `PUT /streams/{id}` отправляется полным потоком: Keitaro не принимает
  частичное обновление, а потеря `triggers`/`landings` сбросила бы настройки;
- «цифры» — клики и конверсии из `POST /report/build` по кампании и офферу.
  Если отчёт недоступен, детали кампании всё равно отдаются: `stats_available`
  = false, цифры нулевые, причина в логе.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.db.directories import (
    entries_from_models,
    list_directory,
    replace_directory,
)
from keitaro_wrapper.db.models import OperationType
from keitaro_wrapper.db.operations import Claim, claim_operation, mark_succeeded
from keitaro_wrapper.keitaro import (
    Campaign,
    KeitaroClient,
    KeitaroError,
    KeitaroNotFound,
    ReportRequest,
    Stream,
    StreamOffer,
    StreamUpdate,
)
from keitaro_wrapper.services.errors import Conflict, NotFound, RequestError, ServiceError
from keitaro_wrapper.services.idempotency import make_key, record_failure, replay
from keitaro_wrapper.services.offers import require_active_offer

logger = logging.getLogger(__name__)

# Диапазоны отчёта, которые понимает Keitaro (docs/keitaro-api.md, RangeRequest)
ReportInterval = Literal[
    "today",
    "yesterday",
    "7_days_ago",
    "first_day_of_this_week",
    "1_month_ago",
    "first_day_of_this_month",
    "1_year_ago",
    "first_day_of_this_year",
    "all_time",
]
DEFAULT_INTERVAL: ReportInterval = "all_time"


class CampaignSummary(BaseModel):
    """Строка списка кампаний."""

    id: int
    name: str
    alias: str
    state: str | None = None
    type: str | None = None


class OfferSummary(BaseModel):
    """Оффер для селекта редактора (из кэша справочника)."""

    id: int
    name: str
    state: str | None = None
    country: list[str] = Field(default_factory=list)


class OfferInStream(BaseModel):
    """Оффер в потоке: доля, состояние и цифры из отчёта."""

    offer_id: int
    name: str | None = None
    share: int
    state: str | None = None
    clicks: int = 0
    conversions: int = 0


class EditorStream(BaseModel):
    """Поток кампании для редактора."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    type: str
    schema_: str | None = Field(default=None, alias="schema")
    position: int | None = None
    weight: int | None = None
    state: str | None = None
    offers: list[OfferInStream] = Field(default_factory=list)


class CampaignDetail(BaseModel):
    """Кампания с потоками и цифрами по офферам."""

    campaign_id: int
    name: str
    alias: str
    state: str | None = None
    type: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None
    streams: list[EditorStream] = Field(default_factory=list)
    clicks: int = 0
    conversions: int = 0
    stats_available: bool = True


class AddOfferRequest(BaseModel):
    """Тело `POST /api/campaigns/{id}/offers`."""

    model_config = ConfigDict(extra="forbid")

    offer_id: int = Field(gt=0)


class OfferShare(BaseModel):
    """Оффер после пересчёта весов."""

    offer_id: int
    share: int
    position: int
    state: str | None = None


class OfferMutationResult(BaseModel):
    """Ответ добавления/удаления оффера; он же хранится в журнале."""

    campaign_id: int
    stream_id: int
    offers: list[OfferShare] = Field(default_factory=list)
    idempotency_key: str
    replayed: bool = False


@dataclass(slots=True)
class _Counters:
    clicks: int = 0
    conversions: int = 0


def distribute_shares(count: int) -> list[int]:
    """Делит 100% поровну; остаток получают первые офферы (сумма всегда 100)."""
    if count <= 0:
        return []
    base, remainder = divmod(100, count)
    return [base + (1 if index < remainder else 0) for index in range(count)]


def _shares(offers: Sequence[StreamOffer]) -> list[StreamOffer]:
    """Нормализует доли офферов: 100% на всех, порядок сохраняется."""
    shares = distribute_shares(len(offers))
    return [
        StreamOffer(offer_id=offer.offer_id, share=shares[index], state=offer.state)
        for index, offer in enumerate(offers)
    ]


def _mutation(
    campaign_id: int, stream_id: int, offers: Sequence[StreamOffer], key: str
) -> OfferMutationResult:
    return OfferMutationResult(
        campaign_id=campaign_id,
        stream_id=stream_id,
        offers=[
            OfferShare(
                offer_id=offer.offer_id,
                share=offer.share,
                position=index + 1,
                state=offer.state,
            )
            for index, offer in enumerate(offers)
        ],
        idempotency_key=key,
    )


async def list_campaigns(session: AsyncSession, client: KeitaroClient) -> list[CampaignSummary]:
    """Список кампаний из Keitaro; заодно обновляет кэш для селектов UI."""
    campaigns = await client.list_campaigns()
    await replace_directory(session, kind="campaigns", entries=entries_from_models(campaigns))
    await session.commit()
    return [
        CampaignSummary(
            id=campaign.id,
            name=campaign.name,
            alias=campaign.alias,
            state=campaign.state,
            type=campaign.type,
        )
        for campaign in sorted(campaigns, key=lambda item: (item.name, item.id))
    ]


async def list_offers(session: AsyncSession, client: KeitaroClient) -> list[OfferSummary]:
    """Список офферов из Keitaro; обновляет кэш `dictionary_cache`."""
    offers = await client.list_offers()
    await replace_directory(session, kind="offers", entries=entries_from_models(offers))
    await session.commit()
    return [
        OfferSummary(id=offer.id, name=offer.name, state=offer.state, country=offer.country)
        for offer in sorted(offers, key=lambda item: item.name)
    ]


async def _cached_offer_names(session: AsyncSession, client: KeitaroClient) -> dict[int, str]:
    """Имена офферов из кэша; при пустом кэше справочник читается один раз."""
    rows = await list_directory(session, "offers")
    if not rows:
        offers = await client.list_offers()
        await replace_directory(session, kind="offers", entries=entries_from_models(offers))
        await session.commit()
        rows = await list_directory(session, "offers")
    names: dict[int, str] = {}
    for row in rows:
        if row.external_id.isdigit():
            names[int(row.external_id)] = row.name
    return names


async def _offer_names(
    session: AsyncSession, client: KeitaroClient, offer_ids: set[int]
) -> dict[int, str]:
    """Имена для офферов потока: кэш + чтение недостающих по одному.

    Оффер мог быть заархивирован: `get_offer` вернёт 404, и это не ошибка
    редактора — оффер останется без имени, но его всё ещё можно удалить.
    """
    names = await _cached_offer_names(session, client)
    for offer_id in offer_ids:
        if offer_id in names:
            continue
        try:
            offer = await client.get_offer(offer_id)
        except KeitaroNotFound:
            continue
        names[offer_id] = offer.name
    return names


async def _report_stats(
    client: KeitaroClient, campaign_id: int, interval: ReportInterval
) -> tuple[dict[str, _Counters], _Counters, bool]:
    """Клики/конверсии по офферам кампании из `POST /report/build`.

    Отчёт умеет измерение `campaign_id` (проверено на живом инстансе), поэтому
    строки фильтруются по id, а не по имени: одинаковые названия кампаний и
    переименование не смешивают цифры. Агрегация внутри кампании — по имени
    оффера, потому что отчёт отдаёт имена, а не id.

    Сбой отчёта не ломает редактор: цифры нулевые, `stats_available=false`,
    причина остаётся в логе.
    """
    try:
        report = await client.build_report(
            ReportRequest(
                dimensions=["campaign_id", "offer"],
                measures=["clicks", "conversions"],
                range={"interval": interval},
            )
        )
    except KeitaroError as exc:
        logger.warning("отчёт Keitaro недоступен для кампании %s: %s", campaign_id, exc)
        return {}, _Counters(), False

    by_offer: dict[str, _Counters] = {}
    totals = _Counters()
    for row in report.rows:
        if row.campaign_id != campaign_id:
            continue
        totals.clicks += row.clicks
        totals.conversions += row.conversions
        if row.offer:
            counters = by_offer.setdefault(row.offer, _Counters())
            counters.clicks += row.clicks
            counters.conversions += row.conversions
    return by_offer, totals, True


async def get_campaign_detail(
    session: AsyncSession,
    client: KeitaroClient,
    campaign_id: int,
    *,
    interval: ReportInterval = DEFAULT_INTERVAL,
) -> CampaignDetail:
    """Кампания, её потоки, офферы со весами и цифры по каждому офферу."""
    campaign: Campaign = await client.get_campaign(campaign_id)
    # Потоки и отчёт независимы: запускаем параллельно, иначе детали ждут
    # сумму двух медленных запросов (на живом инстансе каждый бывает >10 с).
    streams, (by_offer, totals, stats_available) = await asyncio.gather(
        client.list_campaign_streams(campaign_id),
        _report_stats(client, campaign_id, interval),
    )
    offer_ids = {offer.offer_id for stream in streams for offer in stream.offers}
    names = await _offer_names(session, client, offer_ids)

    editor_streams = []
    for stream in streams:
        offers = []
        for offer in stream.offers:
            name = names.get(offer.offer_id)
            counters = by_offer.get(name, _Counters()) if name else _Counters()
            offers.append(
                OfferInStream(
                    offer_id=offer.offer_id,
                    name=name,
                    share=offer.share,
                    state=offer.state,
                    clicks=counters.clicks,
                    conversions=counters.conversions,
                )
            )
        editor_streams.append(
            EditorStream(
                id=stream.id,
                name=stream.name,
                type=stream.type,
                schema_=stream.schema_,
                position=stream.position,
                weight=stream.weight,
                state=stream.state,
                offers=offers,
            )
        )

    return CampaignDetail(
        campaign_id=campaign.id,
        name=campaign.name,
        alias=campaign.alias,
        state=campaign.state,
        type=campaign.type,
        domain_id=campaign.domain_id,
        group_id=campaign.group_id,
        traffic_source_id=campaign.traffic_source_id,
        streams=editor_streams,
        clicks=totals.clicks,
        conversions=totals.conversions,
        stats_available=stats_available,
    )


async def _claim(
    session: AsyncSession,
    *,
    key: str,
    operation_type: OperationType,
    payload: dict[str, Any],
) -> Claim:
    """Регистрирует операцию; невалидный ключ — понятная 422, а не 500."""
    try:
        return await claim_operation(
            session,
            idempotency_key=key,
            operation_type=operation_type,
            payload=payload,
        )
    except ValueError as exc:
        raise RequestError(str(exc)) from exc


async def _offer_stream(
    client: KeitaroClient, campaign_id: int, *, stream_id: int | None = None
) -> Stream:
    """Поток, в котором редактор меняет офферы.

    `stream_id` приходит из формы удаления: один и тот же оффер может стоять
    в нескольких потоках, и действие адресуется конкретному потоку. Без него
    приоритет — `default`-поток (его создаёт создаватор). На живом инстансе
    встречаются кампании, где офферы лежат в `regular`-потоке: если поток с
    офферами ровно один, работаем с ним, а при неоднозначности просим выбрать
    поток в Keitaro — молча менять не тот поток хуже ошибки.
    """
    streams = await client.list_campaign_streams(campaign_id)
    if stream_id is not None:
        for stream in streams:
            if stream.id == stream_id:
                return stream
        # Поток могли удалить в Keitaro уже после открытия страницы.
        raise NotFound(f"в кампании {campaign_id} нет потока {stream_id}")
    defaults = sorted(
        (stream for stream in streams if stream.type == "default"),
        key=lambda stream: (stream.position or 0, stream.id),
    )
    if defaults:
        return defaults[0]

    with_offers = [stream for stream in streams if stream.offers]
    if len(with_offers) == 1:
        return with_offers[0]
    if with_offers:
        raise RequestError(
            "в кампании несколько потоков с офферами — выберите поток в Keitaro вручную"
        )
    raise RequestError(f"в кампании {campaign_id} нет потока с офферами")


def _stream_update(stream: Stream, campaign_id: int, offers: list[StreamOffer]) -> StreamUpdate:
    """Собирает полный `PUT`: меняются только офферы, остальные поля — как были."""
    return StreamUpdate(
        campaign_id=stream.campaign_id or campaign_id,
        name=stream.name,
        type=stream.type,
        schema_=stream.schema_ or "landings",
        action_type=stream.action_type,
        action_payload=stream.action_payload,
        action_options=stream.action_options or None,
        collect_clicks=stream.collect_clicks,
        position=stream.position or 1,
        weight=stream.weight,
        state=stream.state,
        comments=stream.comments,
        filter_or=stream.filter_or,
        offer_selection=stream.offer_selection,
        filters=stream.filters,
        triggers=stream.triggers,
        landings=stream.landings,
        offers=offers,
    )


async def add_offer(
    session: AsyncSession,
    client: KeitaroClient,
    campaign_id: int,
    request: AddOfferRequest,
    *,
    idempotency_key: str | None = None,
) -> OfferMutationResult:
    """Добавляет оффер в поток и пересчитывает веса так, чтобы сумма была 100."""
    key = make_key(idempotency_key)
    payload = {"campaign_id": campaign_id, "offer_id": request.offer_id}
    claim = await _claim(session, key=key, operation_type=OperationType.OFFER_ADD, payload=payload)
    if not claim.created:
        return replay(
            claim.operation,
            payload,
            OfferMutationResult,
            operation_type=OperationType.OFFER_ADD,
        )

    # `pending` должна быть видна другим воркерам до сетевых вызовов
    await session.commit()

    updated = False
    try:
        await require_active_offer(client, request.offer_id)
        stream = await _offer_stream(client, campaign_id)
        if any(offer.offer_id == request.offer_id for offer in stream.offers):
            raise Conflict(f"оффер {request.offer_id} уже есть в потоке {stream.id}")

        offers = _shares([*stream.offers, StreamOffer(offer_id=request.offer_id, share=0)])
        updated_stream = await client.update_stream(
            stream.id, _stream_update(stream, campaign_id, offers)
        )
        updated = True
        result = _mutation(campaign_id, updated_stream.id, offers, claim.operation.idempotency_key)
        await mark_succeeded(
            session,
            claim.operation,
            external_id=str(updated_stream.id),
            result=result.model_dump(mode="json", by_alias=True),
        )
        await session.commit()
        return result
    except ServiceError as exc:
        await record_failure(session, claim.operation, str(exc))
        raise
    except KeitaroError as exc:
        await record_failure(session, claim.operation, str(exc))
        raise
    except Exception as exc:
        if updated:
            # Поток уже изменён: откатывать вслепую нельзя, оставляем след.
            logger.exception(
                "поток кампании %s обновлён, но операция не зафиксирована", campaign_id
            )
        await record_failure(session, claim.operation, f"{type(exc).__name__}: {exc}")
        raise


async def remove_offer(
    session: AsyncSession,
    client: KeitaroClient,
    campaign_id: int,
    offer_id: int,
    *,
    idempotency_key: str | None = None,
    stream_id: int | None = None,
) -> OfferMutationResult:
    """Удаляет оффер из потока и пересчитывает веса оставшихся.

    `stream_id` — поток из формы редактора: один оффер может стоять в
    нескольких потоках, поэтому без него действие было бы неоднозначным.
    Без `stream_id` (API) поток выбирается как раньше — через `_offer_stream`.

    Последний оффер удалить нельзя: поток остался бы без трафика, и кампания
    молча перестала бы конвертить. Ошибка — 422 с объяснением.
    """
    key = make_key(idempotency_key)
    payload: dict[str, Any] = {"campaign_id": campaign_id, "offer_id": offer_id}
    if stream_id is not None:
        # Поток — часть тела операции: удаление из другого потока это другое
        # действие, и повтор с тем же ключом не должен засчитываться как оно.
        # Ключ добавляем только когда он известен: у старых записей журнала
        # поля нет, и их повтор по-прежнему совпадает по телу.
        payload["stream_id"] = stream_id
    claim = await _claim(
        session, key=key, operation_type=OperationType.OFFER_REMOVE, payload=payload
    )
    if not claim.created:
        return replay(
            claim.operation,
            payload,
            OfferMutationResult,
            operation_type=OperationType.OFFER_REMOVE,
        )

    await session.commit()

    updated = False
    try:
        stream = await _offer_stream(client, campaign_id, stream_id=stream_id)
        remaining = [offer for offer in stream.offers if offer.offer_id != offer_id]
        if len(remaining) == len(stream.offers):
            raise NotFound(f"оффер {offer_id} отсутствует в потоке {stream.id}")
        if not remaining:
            raise RequestError("нельзя удалить последний оффер: поток останется без трафика")

        offers = _shares(remaining)
        updated_stream = await client.update_stream(
            stream.id, _stream_update(stream, campaign_id, offers)
        )
        updated = True
        result = _mutation(campaign_id, updated_stream.id, offers, claim.operation.idempotency_key)
        await mark_succeeded(
            session,
            claim.operation,
            external_id=str(updated_stream.id),
            result=result.model_dump(mode="json", by_alias=True),
        )
        await session.commit()
        return result
    except ServiceError as exc:
        await record_failure(session, claim.operation, str(exc))
        raise
    except KeitaroError as exc:
        await record_failure(session, claim.operation, str(exc))
        raise
    except Exception as exc:
        if updated:
            logger.exception(
                "поток кампании %s обновлён, но операция не зафиксирована", campaign_id
            )
        await record_failure(session, claim.operation, f"{type(exc).__name__}: {exc}")
        raise
