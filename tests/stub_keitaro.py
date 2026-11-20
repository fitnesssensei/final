"""Ручной двойник `KeitaroClient` для тестов создавателя.

Никакой сети: вызовы записываются, ответы предсказуемы. `fail_stage` включает
сбой на выбранном шаге, чтобы проверить компенсацию и маппинг ошибок.
"""

from __future__ import annotations

from typing import Any

from keitaro_wrapper.keitaro import (
    Campaign,
    CampaignCreate,
    Domain,
    Group,
    KeitaroAuthError,
    KeitaroNotFound,
    KeitaroUnavailable,
    KeitaroValidationError,
    Offer,
    Stream,
    StreamCreate,
    TrafficSource,
)

CAMPAIGN_ID = 1001
DEFAULT_STREAM_ID = 777


class StubKeitaro:
    """Двойник клиента: те же методы, что использует сервис."""

    def __init__(
        self,
        *,
        domains: list[Domain] | None = None,
        groups: list[Group] | None = None,
        traffic_sources: list[TrafficSource] | None = None,
        offers: dict[int, Offer] | None = None,
        auto_default_stream: bool = False,
        fail_stage: str | None = None,
        compensate_fails: bool = False,
    ) -> None:
        self.domains = [Domain(id=1813, name="example.com")] if domains is None else domains
        self.groups = (
            [Group(id=10332, name="Campaigns", type="campaigns")] if groups is None else groups
        )
        self.traffic_sources = (
            [TrafficSource(id=836, name="Google")] if traffic_sources is None else traffic_sources
        )
        self.offers = (
            {18813: Offer(id=18813, name="Peru", country=["Peru"], state="active")}
            if offers is None
            else offers
        )
        self.auto_default_stream = auto_default_stream
        self.fail_stage = fail_stage
        self.compensate_fails = compensate_fails
        self.calls: list[tuple[str, Any]] = []
        self.deleted: list[int] = []
        self._stream_seq = 500

    @property
    def names(self) -> list[str]:
        """Имена вызовов по порядку — для проверки, что лишних обращений нет."""
        return [name for name, _ in self.calls]

    async def get_offer(self, offer_id: int) -> Offer:
        self.calls.append(("get_offer", offer_id))
        offer = self.offers.get(offer_id)
        if offer is None:
            raise KeitaroNotFound(
                f"GET /offers/{offer_id} -> HTTP 404: Offer not found", status=404
            )
        return offer

    async def list_domains(self) -> list[Domain]:
        self.calls.append(("list_domains", None))
        return self.domains

    async def list_groups(self, group_type: str = "campaigns") -> list[Group]:
        self.calls.append(("list_groups", group_type))
        return self.groups

    async def list_traffic_sources(self) -> list[TrafficSource]:
        self.calls.append(("list_traffic_sources", None))
        return self.traffic_sources

    async def create_campaign(self, payload: CampaignCreate) -> Campaign:
        self.calls.append(("create_campaign", payload))
        if self.fail_stage == "campaign":
            raise KeitaroUnavailable("POST /campaigns -> сеть недоступна: ConnectError")
        if self.fail_stage == "campaign_validation":
            raise KeitaroValidationError(
                "POST /campaigns -> HTTP 422: alias: Alias already exists", status=422
            )
        if self.fail_stage == "campaign_auth":
            raise KeitaroAuthError("POST /campaigns -> HTTP 403: Access denied", status=403)
        return Campaign(
            id=CAMPAIGN_ID,
            name=payload.name,
            alias=payload.alias,
            state=payload.state,
            type=payload.type,
            domain_id=payload.domain_id,
            group_id=payload.group_id,
            traffic_source_id=payload.traffic_source_id,
        )

    async def list_campaign_streams(self, campaign_id: int) -> list[Stream]:
        self.calls.append(("list_campaign_streams", campaign_id))
        if not self.auto_default_stream:
            return []
        return [
            Stream(
                id=DEFAULT_STREAM_ID,
                name="Default",
                type="default",
                campaign_id=campaign_id,
                schema_="landings",
                position=1,
            )
        ]

    def _stream_from(self, stream_id: int, payload: StreamCreate) -> Stream:
        return Stream(
            id=stream_id,
            name=payload.name,
            type=payload.type,
            campaign_id=payload.campaign_id,
            schema_=payload.schema_,
            action_type=payload.action_type,
            action_payload=payload.action_payload,
            collect_clicks=payload.collect_clicks,
            position=payload.position,
            filters=payload.filters,
            offers=payload.offers,
        )

    async def create_stream(self, payload: StreamCreate) -> Stream:
        self.calls.append(("create_stream", payload))
        if self.fail_stage == "google" and payload.type == "regular":
            raise KeitaroUnavailable("POST /streams -> сеть недоступна: ReadTimeout")
        if self.fail_stage == "offer" and payload.type == "default":
            raise KeitaroUnavailable("POST /streams -> HTTP 500: Internal error")
        self._stream_seq += 1
        return self._stream_from(self._stream_seq, payload)

    async def update_stream(self, stream_id: int, payload: StreamCreate) -> Stream:
        self.calls.append(("update_stream", (stream_id, payload)))
        if self.fail_stage == "update":
            raise KeitaroUnavailable("PUT /streams -> HTTP 503: Service Unavailable")
        return self._stream_from(stream_id, payload)

    async def delete_campaign(self, campaign_id: int) -> None:
        self.calls.append(("delete_campaign", campaign_id))
        self.deleted.append(campaign_id)
        if self.compensate_fails:
            raise KeitaroUnavailable("DELETE /campaigns -> HTTP 503")
