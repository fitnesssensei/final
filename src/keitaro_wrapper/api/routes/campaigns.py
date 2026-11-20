"""HTTP-ручка создавателя кампаний."""

from typing import Annotated

from fastapi import APIRouter, Header, Response, status

from keitaro_wrapper.api.deps import KeitaroClientDep, SessionDep
from keitaro_wrapper.config import get_settings
from keitaro_wrapper.services.campaigns import (
    CampaignRequest,
    CampaignResult,
    create_campaign,
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
