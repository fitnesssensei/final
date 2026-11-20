"""Слой доступа к Keitaro Admin API: клиент, DTO и доменные ошибки."""

from keitaro_wrapper.keitaro.client import KeitaroClient
from keitaro_wrapper.keitaro.dto import (
    Campaign,
    CampaignCreate,
    Domain,
    Group,
    GroupType,
    Offer,
    ReportRequest,
    ReportResponse,
    ReportRow,
    Stream,
    StreamCreate,
    StreamFilter,
    StreamOffer,
    StreamUpdate,
    TrafficSource,
)
from keitaro_wrapper.keitaro.errors import (
    KeitaroAuthError,
    KeitaroError,
    KeitaroNotFound,
    KeitaroUnavailable,
    KeitaroValidationError,
)

__all__ = [
    "Campaign",
    "CampaignCreate",
    "Domain",
    "Group",
    "GroupType",
    "KeitaroAuthError",
    "KeitaroClient",
    "KeitaroError",
    "KeitaroNotFound",
    "KeitaroUnavailable",
    "KeitaroValidationError",
    "Offer",
    "ReportRequest",
    "ReportResponse",
    "ReportRow",
    "Stream",
    "StreamCreate",
    "StreamFilter",
    "StreamOffer",
    "StreamUpdate",
    "TrafficSource",
]
