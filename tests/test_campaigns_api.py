"""Тесты ручки `POST /api/campaigns`: коды, идемпотентность, ошибки."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from stub_keitaro import StubKeitaro

from keitaro_wrapper.api.errors import KEITARO_AUTH, KEITARO_UNAVAILABLE
from keitaro_wrapper.db import OperationLog, OperationStatus, create_session_factory
from keitaro_wrapper.main import create_app

ENV = {
    "KEITARO_BASE_URL": "https://example.keitaro.io",
    "KEITARO_API_KEY": "test-key",
    "KEITARO_DOMAIN_ID": "1813",
    "KEITARO_GROUP_ID": "10332",
    "KEITARO_SOURCE_ID": "836",
}
BODY = {"name": "MX test", "geo": "MX", "offer_id": 18813}


@pytest.fixture
def api_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def stub() -> StubKeitaro:
    return StubKeitaro()


@pytest.fixture
async def api_client(
    db_engine: AsyncEngine,
    stub: StubKeitaro,
    api_env: None,
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-клиент поверх приложения с тестовой БД и двойником Keitaro.

    `lifespan` не запускаем: он поднял бы реальный клиент и движок из `.env`.
    Ручка берёт объекты из `app.state`, поэтому достаточно их подменить.
    """
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


async def test_create_campaign_returns_201(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-api"}
    )

    assert response.status_code == 201
    assert response.headers["Idempotency-Key"] == "key-api"
    body = response.json()
    assert body["campaign_id"] == 1001
    assert body["alias"].startswith("mx-test-")
    assert body["geo"] == "MX"
    assert body["offer_id"] == 18813
    assert body["idempotency_key"] == "key-api"
    assert body["replayed"] is False
    assert [stream["type"] for stream in body["streams"]] == ["regular", "default"]
    # В JSON поле называется `schema`, как в Admin API
    assert [stream["schema"] for stream in body["streams"]] == ["redirect", "landings"]


async def test_replay_returns_200_from_journal(
    api_client: httpx.AsyncClient,
    stub: StubKeitaro,
    db_engine: AsyncEngine,
) -> None:
    """Двойной клик: 200, `replayed=true`, второй кампании нет."""
    first = await api_client.post("/api/campaigns", json=BODY, headers={"Idempotency-Key": "dup"})
    second = await api_client.post("/api/campaigns", json=BODY, headers={"Idempotency-Key": "dup"})

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["campaign_id"] == first.json()["campaign_id"]
    assert second.json()["replayed"] is True
    assert stub.names.count("create_campaign") == 1
    assert len(await read_journal(db_engine)) == 1


async def test_missing_header_generates_key(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post("/api/campaigns", json=BODY)

    assert response.status_code == 201
    key = response.json()["idempotency_key"]
    assert len(key) == 32
    assert response.headers["Idempotency-Key"] == key


async def test_invalid_geo_returns_422(
    api_client: httpx.AsyncClient, stub: StubKeitaro, db_engine: AsyncEngine
) -> None:
    response = await api_client.post(
        "/api/campaigns",
        json={**BODY, "geo": "MEX"},
        headers={"Idempotency-Key": "bad-geo"},
    )

    assert response.status_code == 422
    assert "ISO-2" in response.text
    assert stub.calls == []
    assert await read_journal(db_engine) == []


async def test_too_long_idempotency_key_returns_422(api_client: httpx.AsyncClient) -> None:
    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": "k" * 129}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("stage", "status", "detail"),
    [
        ("campaign", 502, KEITARO_UNAVAILABLE),
        ("campaign_auth", 502, KEITARO_AUTH),
    ],
)
async def test_keitaro_errors_are_not_500(
    api_client: httpx.AsyncClient,
    stub: StubKeitaro,
    db_engine: AsyncEngine,
    stage: str,
    status: int,
    detail: str,
) -> None:
    stub.fail_stage = stage

    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": f"key-{stage}"}
    )

    assert response.status_code == status
    assert response.json() == {"detail": detail}
    journal = await read_journal(db_engine)
    assert len(journal) == 1
    assert journal[0].status == OperationStatus.FAILED


async def test_keitaro_validation_error_keeps_field_text(
    api_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """422 трекера показываем как 422: в тексте есть ошибка по полю."""
    stub.fail_stage = "campaign_validation"

    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-validation"}
    )

    assert response.status_code == 422
    assert "alias" in response.json()["detail"]


async def test_stream_failure_compensates_and_returns_502(
    api_client: httpx.AsyncClient, stub: StubKeitaro, db_engine: AsyncEngine
) -> None:
    stub.fail_stage = "google"

    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-comp"}
    )

    assert response.status_code == 502
    assert stub.deleted == [1001]
    journal = await read_journal(db_engine)
    assert journal[0].status == OperationStatus.FAILED


async def test_unknown_offer_returns_422(api_client: httpx.AsyncClient, stub: StubKeitaro) -> None:
    stub.offers = {}

    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-no-offer"}
    )

    assert response.status_code == 422
    assert "оффер 18813 не найден" in response.json()["detail"]


async def test_same_key_different_body_returns_409(api_client: httpx.AsyncClient) -> None:
    await api_client.post("/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-409"})

    response = await api_client.post(
        "/api/campaigns",
        json={**BODY, "name": "Other"},
        headers={"Idempotency-Key": "key-409"},
    )

    assert response.status_code == 409
    assert "другим телом" in response.json()["detail"]


async def test_missing_migrations_return_503(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Пустая SQLite без `alembic upgrade`: 503 с подсказкой, а не 500."""
    for name, value in {
        **ENV,
        "DATABASE_URL": f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}",
    }.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    app.state.keitaro = StubKeitaro()
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'empty.db'}")
    app.state.session_factory = create_session_factory(engine)
    transport = httpx.ASGITransport(app=app)

    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-empty"}
            )
    finally:
        await engine.dispose()

    assert response.status_code == 503
    assert "make migrate" in response.json()["detail"]


async def test_missing_configured_domain_returns_422(
    api_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.domains = []

    response = await api_client.post(
        "/api/campaigns", json=BODY, headers={"Idempotency-Key": "key-domain"}
    )

    assert response.status_code == 422
    assert "домен с id=1813" in response.json()["detail"]
