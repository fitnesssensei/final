"""HTTP-ручки создавателя и редактора кампаний."""

from typing import Annotated

from fastapi import APIRouter, Header, Path, Query, Response, status

from keitaro_wrapper.api.deps import KeitaroClientDep, SessionDep
from keitaro_wrapper.config import get_settings
from keitaro_wrapper.services.campaigns import (
    CampaignRequest,
    CampaignResult,
    create_campaign,
)
from keitaro_wrapper.services.editor import (
    DEFAULT_INTERVAL,
    AddOfferRequest,
    CampaignDetail,
    CampaignSummary,
    OfferMutationResult,
    OfferSummary,
    ReportInterval,
    add_offer,
    get_campaign_detail,
    list_campaigns,
    list_offers,
    remove_offer,
)

router = APIRouter(prefix="/api", tags=["campaigns"])

IdempotencyKey = Annotated[
    str | None,
    Header(
        alias="Idempotency-Key",
        min_length=1,
        max_length=128,
        description=(
            "Ключ идемпотентности: повтор с тем же ключом вернёт результат "
            "первого запроса. Без заголовка сервер сгенерирует ключ сам."
        ),
    ),
]


@router.post(
    "/campaigns",
    response_model=CampaignResult,
    status_code=status.HTTP_201_CREATED,
    summary="Создать кампанию: гео-поток на Google + поток на оффер",
    responses={
        200: {"description": "Повтор с тем же Idempotency-Key: сохранённый результат"},
        409: {"description": "Ключ уже использован с другим телом или операция не завершена"},
        422: {"description": "Невалидное гео, оффер не найден/неактивен, нет сущности из .env"},
        502: {"description": "Keitaro недоступен, отклонил ключ или вернул ошибку"},
    },
)
async def create_campaign_endpoint(
    payload: CampaignRequest,
    response: Response,
    session: SessionDep,
    client: KeitaroClientDep,
    idempotency_key: IdempotencyKey = None,
) -> CampaignResult:
    """Создаёт кампанию и два потока; журнал делает повтор безопасным.

    201 — кампания создана сейчас, 200 — вернулся сохранённый результат
    (поле `replayed`), 409 — ключ занят другой операцией. Ключ возвращается
    в заголовке `Idempotency-Key`, чтобы клиент мог повторить запрос.
    """
    result = await create_campaign(
        session,
        client,
        get_settings(),
        payload,
        idempotency_key=idempotency_key,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["Idempotency-Key"] = result.idempotency_key
    return result


@router.get(
    "/campaigns",
    response_model=list[CampaignSummary],
    summary="Список кампаний (и обновление кэша для селектов)",
    responses={502: {"description": "Keitaro недоступен или отклонил ключ"}},
)
async def campaigns_index(session: SessionDep, client: KeitaroClientDep) -> list[CampaignSummary]:
    """Читает кампании из Keitaro и обновляет локальный кэш справочника."""
    return await list_campaigns(session, client)


@router.get(
    "/offers",
    response_model=list[OfferSummary],
    summary="Список офферов для редактора (и обновление кэша)",
    responses={502: {"description": "Keitaro недоступен или отклонил ключ"}},
)
async def offers_index(session: SessionDep, client: KeitaroClientDep) -> list[OfferSummary]:
    """Читает офферы из Keitaro и обновляет локальный кэш справочника."""
    return await list_offers(session, client)


@router.get(
    "/campaigns/{campaign_id}",
    response_model=CampaignDetail,
    summary="Кампания: потоки, офферы, веса и цифры из отчёта",
    responses={
        404: {"description": "Кампания не найдена в Keitaro"},
        502: {"description": "Keitaro недоступен или вернул ошибку"},
    },
)
async def campaign_detail(
    campaign_id: Annotated[int, Path(gt=0, description="ID кампании в Keitaro")],
    session: SessionDep,
    client: KeitaroClientDep,
    interval: Annotated[
        ReportInterval, Query(description="Диапазон отчёта Keitaro")
    ] = DEFAULT_INTERVAL,
) -> CampaignDetail:
    """Отдаёт потоки и офферы с долями; цифры — из `POST /report/build`.

    Если отчёт недоступен, кампания всё равно возвращается: `stats_available`
    = false, цифры нулевые, причина в логе. Иначе редактор был бы недоступен
    из-за медленного или упавшего отчёта.
    """
    return await get_campaign_detail(session, client, campaign_id, interval=interval)


@router.post(
    "/campaigns/{campaign_id}/offers",
    response_model=OfferMutationResult,
    status_code=status.HTTP_201_CREATED,
    summary="Добавить оффер в поток и пересчитать веса до 100%",
    responses={
        200: {"description": "Повтор с тем же Idempotency-Key: сохранённый результат"},
        404: {"description": "Кампания не найдена в Keitaro"},
        409: {"description": "Оффер уже есть в потоке или ключ занят другой операцией"},
        422: {"description": "Оффер не найден/неактивен, нет потока с офферами"},
        502: {"description": "Keitaro недоступен или вернул ошибку"},
    },
)
async def add_offer_endpoint(
    campaign_id: Annotated[int, Path(gt=0, description="ID кампании в Keitaro")],
    payload: AddOfferRequest,
    response: Response,
    session: SessionDep,
    client: KeitaroClientDep,
    idempotency_key: IdempotencyKey = None,
) -> OfferMutationResult:
    """Веса пересчитываются поровну: 2 оффера — 50/50, 3 — 34/33/33."""
    result = await add_offer(
        session,
        client,
        campaign_id,
        payload,
        idempotency_key=idempotency_key,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["Idempotency-Key"] = result.idempotency_key
    return result


@router.delete(
    "/campaigns/{campaign_id}/offers/{offer_id}",
    response_model=OfferMutationResult,
    summary="Удалить оффер из потока и пересчитать веса",
    responses={
        404: {"description": "Кампания или оффер в потоке не найдены"},
        409: {"description": "Ключ занят другой операцией"},
        422: {"description": "Последний оффер удалить нельзя, нет потока с офферами"},
        502: {"description": "Keitaro недоступен или вернул ошибку"},
    },
)
async def remove_offer_endpoint(
    campaign_id: Annotated[int, Path(gt=0, description="ID кампании в Keitaro")],
    offer_id: Annotated[int, Path(gt=0, description="ID оффера в Keitaro")],
    response: Response,
    session: SessionDep,
    client: KeitaroClientDep,
    idempotency_key: IdempotencyKey = None,
) -> OfferMutationResult:
    """Повтор с тем же ключом вернёт сохранённый результат и не тронет Keitaro."""
    result = await remove_offer(
        session,
        client,
        campaign_id,
        offer_id,
        idempotency_key=idempotency_key,
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    response.headers["Idempotency-Key"] = result.idempotency_key
    return result
