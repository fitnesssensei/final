"""Создаватель кампаний: два потока, идемпотентность и компенсация.

Сценарий (контракт — `docs/campaign-create.md`, проверен на живом инстансе):

1. валидируем запрос и справочники (домен, группа, источник — из кэша шага 3,
   с обновлением при промахе);
2. регистрируем операцию в журнале и коммитим её **до** сетевых вызовов;
3. создаём кампанию, гео-поток на Google и поток с оффером; `default`-поток,
   который Keitaro мог создать сам, переиспользуем через `PUT`, иначе получится
   два дефолтных;
4. при сбое архивируем уже созданную кампанию и пишем ошибку в журнал;
5. повтор с тем же `Idempotency-Key` возвращает сохранённый результат и не
   трогает Keitaro.
"""

from __future__ import annotations

import logging
import re
import secrets
import string
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.config import Settings
from keitaro_wrapper.db.directories import DirectoryEntry, list_directory, replace_directory
from keitaro_wrapper.db.models import DictionaryCache, OperationLog, OperationStatus, OperationType
from keitaro_wrapper.db.operations import claim_operation, mark_failed, mark_succeeded
from keitaro_wrapper.keitaro import (
    Campaign,
    CampaignCreate,
    KeitaroClient,
    KeitaroError,
    KeitaroNotFound,
    Stream,
    StreamCreate,
    StreamFilter,
    StreamOffer,
)

logger = logging.getLogger(__name__)

GEO_RE = re.compile(r"^[A-Z]{2}$")
ALIAS_SLUG_LIMIT = 24
ALIAS_SUFFIX_LIMIT = 6
OFFER_SHARE = 100


class CampaignError(Exception):
    """Ошибка сценария создания кампании, которую надо показать клиенту."""

    status_code = 422


class CampaignRequestError(CampaignError):
    """Запрос не выполним: нет сущности, оффер неактивен и т.п. (422)."""

    status_code = 422


class IdempotencyConflict(CampaignError):
    """Ключ уже использован и повторять операцию нельзя (409)."""

    status_code = 409


class CampaignRequest(BaseModel):
    """Тело `POST /api/campaigns`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)
    geo: str
    offer_id: int = Field(gt=0)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("название кампании не может быть пустым")
        return value

    @field_validator("geo")
    @classmethod
    def _validate_geo(cls, value: str) -> str:
        """Гео — ISO-2: именно этот код попадает в фильтр `country` потока."""
        geo = value.strip().upper()
        if not GEO_RE.fullmatch(geo):
            raise ValueError("гео-код должен быть ISO-2: две латинские буквы, например MX")
        return geo


class StreamRef(BaseModel):
    """Созданный поток в ответе API."""

    model_config = ConfigDict(populate_by_name=True)

    id: int
    name: str
    type: str
    schema_: str | None = Field(default=None, alias="schema")
    position: int | None = None


class CampaignResult(BaseModel):
    """Ответ на создание кампании; он же хранится в журнале для повтора."""

    model_config = ConfigDict(populate_by_name=True)

    campaign_id: int
    alias: str
    name: str
    geo: str
    offer_id: int
    streams: list[StreamRef] = Field(default_factory=list)
    idempotency_key: str
    replayed: bool = False


def make_alias(name: str) -> str:
    """Alias из названия со случайным суффиксом.

    Суффикс обязателен: на инстансе параллельно работают другие тестировщики,
    и повторяющийся alias перезаписал бы чужую кампанию (уникальность держит
    Keitaro). Кириллица в slug не попадает — тогда остаётся `campaign`.
    """
    slug = re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-").lower()[:ALIAS_SLUG_LIMIT]
    suffix = "".join(
        secrets.choice(string.ascii_lowercase + string.digits) for _ in range(ALIAS_SUFFIX_LIMIT)
    )
    return f"{slug or 'campaign'}-{suffix}"


def google_redirect(settings: Settings) -> str:
    """URL Google без завершающего слэша: HttpUrl добавляет его к пустому пути."""
    return str(settings.keitaro_google_url).rstrip("/")


def build_campaign(
    *, alias: str, name: str, domain_id: int, group_id: int, traffic_source_id: int
) -> CampaignCreate:
    """Тело `POST /campaigns` — ровно как в проверенном контракте."""
    return CampaignCreate(
        alias=alias,
        name=name,
        type="position",
        state="active",
        domain_id=domain_id,
        group_id=group_id,
        traffic_source_id=traffic_source_id,
    )


def build_google_stream(campaign_id: int, geo: str, google_url: str) -> StreamCreate:
    """Поток 1: единственный фильтр `country = geo` (include) → Google."""
    return StreamCreate(
        campaign_id=campaign_id,
        name=f"Google ({geo})",
        type="regular",
        schema_="redirect",
        action_type="http",
        action_payload=google_url,
        collect_clicks=True,
        position=1,
        filters=[StreamFilter(name="country", mode="accept", payload=[geo])],
        offers=[],
    )


def build_offer_stream(campaign_id: int, offer_id: int) -> StreamCreate:
    """Поток 2: без фильтров (fallback) → оффер со 100% трафика."""
    return StreamCreate(
        campaign_id=campaign_id,
        name="Offers",
        type="default",
        schema_="landings",
        action_type="http",
        action_payload="",
        collect_clicks=True,
        position=2,
        filters=[],
        offers=[StreamOffer(offer_id=offer_id, share=OFFER_SHARE)],
    )


def _directory_entries(items: Sequence[Any]) -> list[DirectoryEntry]:
    """Переводит DTO Keitaro в записи кэша: id строкой, исходный JSON целиком."""
    return [
        DirectoryEntry(
            external_id=str(item.id), name=item.name, payload=item.model_dump(mode="json")
        )
        for item in items
    ]


async def _refresh_directory(session: AsyncSession, client: KeitaroClient, kind: str) -> None:
    """Читает справочник из Keitaro и полностью заменяет локальный кэш."""
    if kind == "domains":
        items: list[Any] = await client.list_domains()
    elif kind == "groups":
        # `type` обязателен, иначе живой инстанс отвечает 400
        items = await client.list_groups("campaigns")
    elif kind == "traffic_sources":
        items = await client.list_traffic_sources()
    else:
        raise ValueError(f"неизвестный справочник: {kind!r}")
    await replace_directory(session, kind=kind, entries=_directory_entries(items))


def _find_entry(
    rows: Sequence[DictionaryCache], entity_id: int, kind: str
) -> DictionaryCache | None:
    """Ищет запись по внешнему id; для групп отсекает офферные группы."""
    wanted = str(entity_id)
    for row in rows:
        if row.external_id != wanted:
            continue
        if kind == "groups" and row.payload.get("type") not in (None, "campaigns"):
            continue
        return row
    return None


async def _require_entry(
    session: AsyncSession,
    client: KeitaroClient,
    *,
    kind: str,
    entity_id: int,
    label: str,
) -> DictionaryCache:
    """Проверяет, что сущность из `.env` есть в Keitaro.

    Сначала смотрим кэш; при промахе обновляем справочник один раз. Ошибка
    вместо создания кампании с несуществующим доменом: Keitaro отклонил бы
    такой POST невнятной 422 уже после записи в журнал.
    """
    rows = await list_directory(session, kind)
    entry = _find_entry(rows, entity_id, kind)
    if entry is None:
        await _refresh_directory(session, client, kind)
        entry = _find_entry(await list_directory(session, kind), entity_id, kind)
    if entry is None:
        raise CampaignRequestError(
            f"{label} с id={entity_id} отсутствует в Keitaro — проверьте KEITARO_* в .env"
        )
    return entry


async def _validate_directories(
    session: AsyncSession, client: KeitaroClient, settings: Settings
) -> None:
    """Домен, группа и источник по умолчанию должны существовать в Keitaro."""
    await _require_entry(
        session, client, kind="domains", entity_id=settings.keitaro_domain_id, label="домен"
    )
    await _require_entry(
        session, client, kind="groups", entity_id=settings.keitaro_group_id, label="группа"
    )
    await _require_entry(
        session,
        client,
        kind="traffic_sources",
        entity_id=settings.keitaro_source_id,
        label="источник трафика",
    )


async def _validate_offer(client: KeitaroClient, offer_id: int) -> None:
    """Оффер читается напрямую: кэш мог устареть, а кампания — нет.

    404 Keitaro превращается в понятную 422 (в запросе неверный `offer_id`),
    а не в 404 «ручка не найдена».
    """
    try:
        offer = await client.get_offer(offer_id)
    except KeitaroNotFound as exc:
        raise CampaignRequestError(f"оффер {offer_id} не найден в Keitaro") from exc
    if offer.state != "active":
        raise CampaignRequestError(
            f"оффер {offer_id} не активен: state={offer.state or 'не указан'}"
        )


async def _create_streams(
    client: KeitaroClient, campaign: Campaign, request: CampaignRequest, settings: Settings
) -> list[Stream]:
    """Создаёт гео-поток и поток оффера, переиспользуя автосозданный default."""
    existing = await client.list_campaign_streams(campaign.id)
    default_stream = next((stream for stream in existing if stream.type == "default"), None)

    google = await client.create_stream(
        build_google_stream(campaign.id, request.geo, google_redirect(settings))
    )
    if default_stream is None:
        offer = await client.create_stream(build_offer_stream(campaign.id, request.offer_id))
    else:
        offer = await client.update_stream(
            default_stream.id, build_offer_stream(campaign.id, request.offer_id)
        )
    return [google, offer]


async def _compensate(client: KeitaroClient, campaign: Campaign | None) -> str:
    """Архивирует созданную кампанию; возвращает примечание для журнала.

    Компенсация best-effort: если Keitaro недоступен, исходная ошибка важнее,
    но текст «удалите вручную» попадёт в журнал — иначе кампания останется
    незамеченной.
    """
    if campaign is None:
        return ""
    try:
        await client.delete_campaign(campaign.id)
    except KeitaroError as exc:
        logger.error(
            "компенсация не удалась для кампании %s: %s — удалите вручную", campaign.id, exc
        )
        return f"; компенсация не удалась — удалите кампанию {campaign.id} вручную"
    logger.warning("компенсация: кампания %s перемещена в архив", campaign.id)
    return ""


async def _record_failure(session: AsyncSession, operation: OperationLog, error: str) -> None:
    """Пишет ошибку в журнал; сбой записи не должен подменять исходное исключение."""
    try:
        await mark_failed(session, operation, error=error)
        await session.commit()
    except Exception:
        logger.exception("не удалось записать ошибку операции %s", operation.idempotency_key)
        await session.rollback()


def _replay(operation: OperationLog, payload: dict[str, Any]) -> CampaignResult:
    """Возвращает сохранённый результат или объясняет, почему повтор запрещён."""
    if operation.payload != payload:
        raise IdempotencyConflict(
            "Idempotency-Key уже использован с другим телом запроса — "
            "для новой кампании нужен новый ключ"
        )
    if operation.status == OperationStatus.SUCCEEDED.value:
        if operation.result is None:
            raise IdempotencyConflict(
                f"кампания {operation.external_id} создана, но результат не сохранён: "
                "проверьте её в Keitaro"
            )
        stored = CampaignResult.model_validate(operation.result)
        return stored.model_copy(update={"replayed": True})
    if operation.status == OperationStatus.PENDING.value:
        raise IdempotencyConflict(
            "операция с этим Idempotency-Key ещё выполняется — повторите запрос позже"
        )
    raise IdempotencyConflict(
        f"операция с этим Idempotency-Key завершилась ошибкой: "
        f"{operation.error or 'без текста'}; исправьте причину и повторите с новым ключом"
    )


async def create_campaign(
    session: AsyncSession,
    client: KeitaroClient,
    settings: Settings,
    request: CampaignRequest,
    *,
    idempotency_key: str | None = None,
) -> CampaignResult:
    """Создаёт кампанию по контракту; повтор с тем же ключом не создаёт дубль.

    Границы транзакций важны: claim коммитится **до** вызова Keitaro, иначе
    параллельный запрос с тем же ключом не увидел бы `pending` и тоже создал бы
    кампанию. Каждая точка отказа после создания кампании компенсируется.
    """
    key = (idempotency_key or "").strip() or uuid4().hex
    payload = request.model_dump(mode="json")
    try:
        claim = await claim_operation(
            session,
            idempotency_key=key,
            operation_type=OperationType.CAMPAIGN_CREATE,
            payload=payload,
        )
    except ValueError as exc:
        raise CampaignRequestError(str(exc)) from exc

    if not claim.created:
        return _replay(claim.operation, payload)

    # `pending` должна быть видна другим воркерам до сетевых вызовов
    await session.commit()

    campaign: Campaign | None = None
    campaign_ready = False
    try:
        await _validate_directories(session, client, settings)
        await _validate_offer(client, request.offer_id)
        # Отпускаем соединение с БД до сетевых вызовов: иначе транзакция,
        # открытая чтением справочников, держит его всё время создания потоков.
        await session.commit()
        campaign = await client.create_campaign(
            build_campaign(
                alias=make_alias(request.name),
                name=request.name,
                domain_id=settings.keitaro_domain_id,
                group_id=settings.keitaro_group_id,
                traffic_source_id=settings.keitaro_source_id,
            )
        )
        streams = await _create_streams(client, campaign, request, settings)
        campaign_ready = True
        result = CampaignResult(
            campaign_id=campaign.id,
            alias=campaign.alias,
            name=campaign.name,
            geo=request.geo,
            offer_id=request.offer_id,
            streams=[
                StreamRef(
                    id=stream.id,
                    name=stream.name,
                    type=stream.type,
                    schema_=stream.schema_,
                    position=stream.position,
                )
                for stream in streams
            ],
            idempotency_key=claim.operation.idempotency_key,
        )
        await mark_succeeded(
            session,
            claim.operation,
            external_id=str(campaign.id),
            result=result.model_dump(mode="json", by_alias=True),
        )
        await session.commit()
        return result
    except CampaignError as exc:
        # Ошибка до создания кампании: компенсировать нечего
        await _record_failure(session, claim.operation, str(exc))
        raise
    except KeitaroError as exc:
        note = "" if campaign_ready else await _compensate(client, campaign)
        await _record_failure(session, claim.operation, f"{exc}{note}")
        raise
    except Exception as exc:
        if campaign_ready:
            # Кампания и потоки уже созданы — удалять их из-за сбоя журнала нельзя:
            # пользователь потеряет рабочую кампанию. Оставляем след в логе и
            # в тексте ошибки, чтобы её нашли руками.
            logger.exception(
                "кампания %s создана, но операция не зафиксирована в журнале",
                campaign.id if campaign else "?",
            )
            note = "; кампания создана, но результат не сохранён — проверьте её в Keitaro"
        else:
            note = await _compensate(client, campaign)
        await _record_failure(session, claim.operation, f"{type(exc).__name__}: {exc}{note}")
        raise
