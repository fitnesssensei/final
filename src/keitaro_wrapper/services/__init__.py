"""Бизнес-сценарии поверх Keitaro и локальной БД."""

from keitaro_wrapper.services.campaigns import (
    CampaignRequest,
    CampaignResult,
    StreamRef,
    create_campaign,
)
from keitaro_wrapper.services.editor import (
    AddOfferRequest,
    CampaignDetail,
    CampaignSummary,
    EditorStream,
    OfferInStream,
    OfferMutationResult,
    OfferShare,
    OfferSummary,
    add_offer,
    get_campaign_detail,
    list_campaigns,
    list_offers,
    remove_offer,
)
from keitaro_wrapper.services.errors import (
    Conflict,
    NotFound,
    RequestError,
    ServiceError,
)
from keitaro_wrapper.services.idempotency import make_key, record_failure, replay

__all__ = [
    "AddOfferRequest",
    "CampaignDetail",
    "CampaignRequest",
    "CampaignResult",
    "CampaignSummary",
    "Conflict",
    "EditorStream",
    "NotFound",
    "OfferInStream",
    "OfferMutationResult",
    "OfferShare",
    "OfferSummary",
    "RequestError",
    "ServiceError",
    "StreamRef",
    "add_offer",
    "create_campaign",
    "get_campaign_detail",
    "list_campaigns",
    "list_offers",
    "make_key",
    "record_failure",
    "remove_offer",
    "replay",
]
