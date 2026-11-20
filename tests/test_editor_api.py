"""Тесты ручек редактора: список, детали с цифрами, add/remove офферов."""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine
from stub_keitaro import (
    CAMPAIGN_ID,
    DEFAULT_STREAMS,
    OFFER_ID,
    SECOND_OFFER_ID,
    StubKeitaro,
)

from keitaro_wrapper.db import OperationLog, OperationStatus, create_session_factory
from keitaro_wrapper.keitaro import Stream, StreamOffer
from keitaro_wrapper.main import create_app

ENV = {
    "KEITARO_BASE_URL": "https://example.keitaro.io",
    "KEITARO_API_KEY": "test-key",
    "KEITARO_DOMAIN_ID": "1813",
    "KEITARO_GROUP_ID": "10332",
    "KEITARO_SOURCE_ID": "836",
}


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def stub() -> StubKeitaro:
    return StubKeitaro(streams=DEFAULT_STREAMS)


@pytest.fixture
async def api_client(
    db_engine: AsyncEngine, stub: StubKeitaro, api_env: None
) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()
    app.state.session_factory = create_session_factory(db_engine)
    app.state.keitaro = stub
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


async def read_journal(db_engine: AsyncEngine) -> list[OperationLog]:
    factory = create_session_factory(db_engine)
    async with factory() as session:
        return list(await session.scalars(select(OperationLog)))


async def test_campaigns_list(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/campaigns")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": CAMPAIGN_ID,
            "name": "MX test",
            "alias": "mx-test-ab12cd",
            "state": "active",
            "type": "position",
        }
    ]


async def test_offers_list(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/offers")

    assert response.status_code == 200
    body = response.json()
    assert [offer["id"] for offer in body] == [OFFER_ID, SECOND_OFFER_ID]
    assert body[0]["country"] == ["Peru"]


async def test_campaign_detail_with_stats(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(f"/api/campaigns/{CAMPAIGN_ID}")

    assert response.status_code == 200
    body = response.json()
    assert body["campaign_id"] == CAMPAIGN_ID
    assert body["stats_available"] is True
    assert (body["clicks"], body["conversions"]) == (15, 2)
    assert [stream["type"] for stream in body["streams"]] == ["regular", "default"]
    assert body["streams"][1]["offers"] == [
        {
            "offer_id": OFFER_ID,
            "name": "Peru",
            "share": 100,
            "state": "active",
            "clicks": 10,
            "conversions": 2,
        }
    ]
    # Поле `schema`, как в Admin API
    assert body["streams"][0]["schema"] == "redirect"


async def test_detail_interval_query(api_client: httpx.AsyncClient, stub: StubKeitaro) -> None:
    response = await api_client.get(
        f"/api/campaigns/{CAMPAIGN_ID}", params={"interval": "7_days_ago"}
    )

    assert response.status_code == 200
    report = next(payload for name, payload in stub.calls if name == "build_report")
    assert report.range == {"interval": "7_days_ago"}


async def test_detail_survives_report_failure(
    api_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.report_fails = True

    response = await api_client.get(f"/api/campaigns/{CAMPAIGN_ID}")

    assert response.status_code == 200
    assert response.json()["stats_available"] is False


async def test_detail_missing_campaign_returns_404(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get("/api/campaigns/999999")

    assert response.status_code == 404
    assert "не найдено" in response.json()["detail"]


async def test_invalid_interval_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.get(
        f"/api/campaigns/{CAMPAIGN_ID}", params={"interval": "all_time_please"}
    )

    assert response.status_code == 422


async def test_add_offer_returns_201(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": SECOND_OFFER_ID},
        headers={"Idempotency-Key": "api-add"},
    )

    assert response.status_code == 201
    body = response.json()
    assert response.headers["Idempotency-Key"] == "api-add"
    assert [(offer["offer_id"], offer["share"]) for offer in body["offers"]] == [
        (OFFER_ID, 50),
        (SECOND_OFFER_ID, 50),
    ]
    assert body["replayed"] is False


async def test_add_offer_replay_returns_200(
    api_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    first = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": SECOND_OFFER_ID},
        headers={"Idempotency-Key": "api-add-replay"},
    )
    second = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": SECOND_OFFER_ID},
        headers={"Idempotency-Key": "api-add-replay"},
    )

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["offers"] == first.json()["offers"]
    assert second.json()["replayed"] is True
    assert stub.names.count("update_stream") == 1


async def test_add_existing_offer_returns_409(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": OFFER_ID},
        headers={"Idempotency-Key": "api-dup"},
    )

    assert response.status_code == 409
    assert "уже есть" in response.json()["detail"]


async def test_add_unknown_offer_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": 999999},
        headers={"Idempotency-Key": "api-unknown"},
    )

    assert response.status_code == 422
    assert "не найден" in response.json()["detail"]


async def test_add_offer_without_key_generates_it(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers", json={"offer_id": SECOND_OFFER_ID}
    )

    assert response.status_code == 201
    key = response.json()["idempotency_key"]
    assert len(key) == 32
    assert response.headers["Idempotency-Key"] == key


def two_offer_stream() -> list[Stream]:
    return [
        Stream(
            id=2,
            name="Offers",
            type="default",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=2,
            offers=[
                StreamOffer(offer_id=OFFER_ID, share=50, state="active"),
                StreamOffer(offer_id=SECOND_OFFER_ID, share=50, state="active"),
            ],
        )
    ]


async def test_remove_offer_returns_rebalanced_weights(
    api_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.streams = two_offer_stream()

    response = await api_client.delete(
        f"/api/campaigns/{CAMPAIGN_ID}/offers/{SECOND_OFFER_ID}",
        headers={"Idempotency-Key": "api-remove"},
    )

    assert response.status_code == 200
    assert [(offer["offer_id"], offer["share"]) for offer in response.json()["offers"]] == [
        (OFFER_ID, 100)
    ]


async def test_remove_offer_replay(api_client: httpx.AsyncClient, stub: StubKeitaro) -> None:
    stub.streams = two_offer_stream()
    first = await api_client.delete(
        f"/api/campaigns/{CAMPAIGN_ID}/offers/{SECOND_OFFER_ID}",
        headers={"Idempotency-Key": "api-remove-replay"},
    )
    second = await api_client.delete(
        f"/api/campaigns/{CAMPAIGN_ID}/offers/{SECOND_OFFER_ID}",
        headers={"Idempotency-Key": "api-remove-replay"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert second.json()["offers"] == first.json()["offers"]
    assert stub.names.count("update_stream") == 1


async def test_remove_last_offer_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.delete(
        f"/api/campaigns/{CAMPAIGN_ID}/offers/{OFFER_ID}",
        headers={"Idempotency-Key": "api-last"},
    )

    assert response.status_code == 422
    assert "последний оффер" in response.json()["detail"]


async def test_remove_missing_offer_returns_404(api_client: httpx.AsyncClient) -> None:
    response = await api_client.delete(
        f"/api/campaigns/{CAMPAIGN_ID}/offers/{SECOND_OFFER_ID}",
        headers={"Idempotency-Key": "api-missing"},
    )

    assert response.status_code == 404


async def test_update_failure_returns_502_and_marks_journal(
    api_client: httpx.AsyncClient, stub: StubKeitaro, db_engine: AsyncEngine
) -> None:
    stub.fail_stage = "update"

    response = await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": SECOND_OFFER_ID},
        headers={"Idempotency-Key": "api-put-fail"},
    )

    assert response.status_code == 502
    journal = await read_journal(db_engine)
    assert len(journal) == 1
    assert journal[0].status == OperationStatus.FAILED


async def test_add_key_cannot_be_reused_for_remove(api_client: httpx.AsyncClient) -> None:
    """Один ключ — одна операция: тип операции тоже входит в проверку."""
    await api_client.post(
        f"/api/campaigns/{CAMPAIGN_ID}/offers",
        json={"offer_id": SECOND_OFFER_ID},
        headers={"Idempotency-Key": "api-shared"},
    )

    response = await api_client.delete(
        f"/api/campaigns/{CAMPAIGN_ID}/offers/{SECOND_OFFER_ID}",
        headers={"Idempotency-Key": "api-shared"},
    )

    assert response.status_code == 409
    assert "другим телом" in response.json()["detail"]
