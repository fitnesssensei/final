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
    ReportRequest,
    ReportResponse,
    ReportRow,
    Stream,
    StreamCreate,
    StreamOffer,
    StreamUpdate,
    TrafficSource,
)

CAMPAIGN_ID = 1001
DEFAULT_STREAM_ID = 777
OFFER_ID = 18813
SECOND_OFFER_ID = 18814
DEFAULT_CAMPAIGN = Campaign(
    id=CAMPAIGN_ID,
    name="MX test",
    alias="mx-test-ab12cd",
    state="active",
    type="position",
    domain_id=1813,
    group_id=10332,
    traffic_source_id=836,
)
DEFAULT_STREAMS = [
    Stream(
        id=1,
        name="Google (MX)",
        type="regular",
        campaign_id=CAMPAIGN_ID,
        schema_="redirect",
        action_type="http",
        position=1,
        weight=100,
    ),
    Stream(
        id=2,
        name="Offers",
        type="default",
        campaign_id=CAMPAIGN_ID,
        schema_="landings",
        action_type="http",
        position=2,
        weight=100,
        offers=[StreamOffer(offer_id=OFFER_ID, share=100, state="active")],
    ),
]
DEFAULT_REPORT_ROWS = [
    ReportRow(campaign_id=CAMPAIGN_ID, offer=None, clicks=5, conversions=0),
    ReportRow(campaign_id=CAMPAIGN_ID, offer="Peru", clicks=10, conversions=2),
    ReportRow(campaign_id=999, offer="Peru", clicks=99, conversions=9),
]


class StubKeitaro:
    """Двойник клиента: те же методы, что использует сервис."""

    def __init__(
        self,
        *,
        domains: list[Domain] | None = None,
        groups: list[Group] | None = None,
        traffic_sources: list[TrafficSource] | None = None,
        offers: dict[int, Offer] | None = None,
        campaigns: list[Campaign] | None = None,
        streams: list[Stream] | None = None,
        report_rows: list[ReportRow] | None = None,
        auto_default_stream: bool = False,
        report_fails: bool = False,
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
            {
                OFFER_ID: Offer(id=OFFER_ID, name="Peru", country=["Peru"], state="active"),
                SECOND_OFFER_ID: Offer(
                    id=SECOND_OFFER_ID, name="Second", country=["Mexico"], state="active"
                ),
            }
            if offers is None
            else offers
        )
        self.campaigns = [DEFAULT_CAMPAIGN] if campaigns is None else campaigns
        # Пустой список по умолчанию: создаватель проверяет, что Keitaro сам
        # завёл default-поток, только когда включён `auto_default_stream`.
        self.streams = [] if streams is None else streams
        self.report_rows = DEFAULT_REPORT_ROWS if report_rows is None else report_rows
        self.auto_default_stream = auto_default_stream
        self.report_fails = report_fails
        self.fail_stage = fail_stage
        self.compensate_fails = compensate_fails
        self.calls: list[tuple[str, Any]] = []
        self.deleted: list[int] = []
        self._stream_seq = 500

    @property
    def names(self) -> list[str]:
        """Имена вызовов по порядку — для проверки, что лишних обращений нет."""
        return [name for name, _ in self.calls]

    async def list_offers(self) -> list[Offer]:
        self.calls.append(("list_offers", None))
        return list(self.offers.values())

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

    async def list_campaigns(self) -> list[Campaign]:
        self.calls.append(("list_campaigns", None))
        return self.campaigns

    async def get_campaign(self, campaign_id: int) -> Campaign:
        self.calls.append(("get_campaign", campaign_id))
        for campaign in self.campaigns:
            if campaign.id == campaign_id:
                return campaign
        raise KeitaroNotFound(
            f"GET /campaigns/{campaign_id} -> HTTP 404: Campaign not found", status=404
        )

    async def build_report(self, payload: ReportRequest) -> ReportResponse:
        self.calls.append(("build_report", payload))
        if self.report_fails:
            raise KeitaroUnavailable("POST /report/build -> HTTP 503: Service Unavailable")
        return ReportResponse(rows=self.report_rows, total=len(self.report_rows))

    async def list_campaign_streams(self, campaign_id: int) -> list[Stream]:
        self.calls.append(("list_campaign_streams", campaign_id))
        if self.auto_default_stream:
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
        return list(self.streams)

    def _stream_from(self, stream_id: int, payload: StreamCreate | StreamUpdate) -> Stream:
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
            weight=getattr(payload, "weight", None),
            state=getattr(payload, "state", None),
            filter_or=getattr(payload, "filter_or", None),
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

    async def update_stream(self, stream_id: int, payload: StreamCreate | StreamUpdate) -> Stream:
        self.calls.append(("update_stream", (stream_id, payload)))
        if self.fail_stage == "update":
            raise KeitaroUnavailable("PUT /streams -> HTTP 503: Service Unavailable")
        updated = self._stream_from(stream_id, payload)
        self.streams = [updated if stream.id == stream_id else stream for stream in self.streams]
        return updated

    async def delete_campaign(self, campaign_id: int) -> None:
        self.calls.append(("delete_campaign", campaign_id))
        self.deleted.append(campaign_id)
        if self.compensate_fails:
            raise KeitaroUnavailable("DELETE /campaigns -> HTTP 503")
