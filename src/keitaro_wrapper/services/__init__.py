"""Бизнес-сценарии поверх Keitaro и локальной БД."""

from keitaro_wrapper.services.campaigns import (
    CampaignError,
    CampaignRequest,
    CampaignRequestError,
    CampaignResult,
    IdempotencyConflict,
    StreamRef,
    create_campaign,
)

__all__ = [
    "CampaignError",
    "CampaignRequest",
    "CampaignRequestError",
    "CampaignResult",
    "IdempotencyConflict",
    "StreamRef",
    "create_campaign",
]
