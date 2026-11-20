"""Проверки офферов, общие для создавателя и редактора."""

from __future__ import annotations

from keitaro_wrapper.keitaro import KeitaroClient, KeitaroNotFound, Offer
from keitaro_wrapper.services.errors import RequestError


async def require_active_offer(client: KeitaroClient, offer_id: int) -> Offer:
    """Читает оффер напрямую: кэш мог устареть, а кампания — нет.

    404 Keitaro превращается в понятную 422 (в запросе неверный `offer_id`),
    а не в 404 «ручка не найдена». Неактивный оффер отклоняем до записи.
    """
    try:
        offer = await client.get_offer(offer_id)
    except KeitaroNotFound as exc:
        raise RequestError(f"оффер {offer_id} не найден в Keitaro") from exc
    if offer.state != "active":
        raise RequestError(f"оффер {offer_id} не активен: state={offer.state or 'не указан'}")
    return offer
