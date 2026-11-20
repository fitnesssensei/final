"""Тесты создавателя кампаний: payload'ы, журнал, компенсация, повтор."""

import re

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from stub_keitaro import DEFAULT_STREAM_ID, StubKeitaro

from keitaro_wrapper.config import Settings
from keitaro_wrapper.db import OperationLog, OperationStatus
from keitaro_wrapper.db.directories import DirectoryEntry, replace_directory
from keitaro_wrapper.db.models import OperationType
from keitaro_wrapper.db.operations import claim_operation, find_operation
from keitaro_wrapper.keitaro import Domain, KeitaroUnavailable, Offer
from keitaro_wrapper.services.campaigns import (
    CampaignRequest,
    CampaignRequestError,
    IdempotencyConflict,
    build_campaign,
    build_google_stream,
    build_offer_stream,
    create_campaign,
    google_redirect,
    make_alias,
)

GOOGLE_URL = "https://www.google.com"


@pytest.fixture
def settings() -> Settings:
    """Настройки инстанса: значения из проверенного контракта."""
    return Settings(
        keitaro_base_url="https://example.keitaro.io",
        keitaro_api_key="test-key",
        keitaro_domain_id=1813,
        keitaro_group_id=10332,
        keitaro_source_id=836,
    )


@pytest.fixture
def stub() -> StubKeitaro:
    return StubKeitaro()


@pytest.fixture
def request_payload() -> CampaignRequest:
    return CampaignRequest(name="MX test", geo="mx", offer_id=18813)


async def seed_directories(session: AsyncSession) -> None:
    """Заполняет кэш справочников, чтобы сервис не ходил в Keitaro за ними."""
    await replace_directory(
        session,
        kind="domains",
        entries=[DirectoryEntry(external_id="1813", name="example.com", payload={"id": 1813})],
    )
    await replace_directory(
        session,
        kind="groups",
        entries=[
            DirectoryEntry(
                external_id="10332",
                name="Campaigns",
                payload={"id": 10332, "type": "campaigns"},
            )
        ],
    )
    await replace_directory(
        session,
        kind="traffic_sources",
        entries=[DirectoryEntry(external_id="836", name="Google", payload={"id": 836})],
    )
    await session.commit()


def test_campaign_payload_matches_contract() -> None:
    """Точная форма `POST /campaigns` из docs/campaign-create.md."""
    payload = build_campaign(
        alias="mx-test-ab12cd",
        name="MX test",
        domain_id=1813,
        group_id=10332,
        traffic_source_id=836,
    )

    assert payload.model_dump() == {
        "alias": "mx-test-ab12cd",
        "name": "MX test",
        "type": "position",
        "state": "active",
        "domain_id": 1813,
        "group_id": 10332,
        "traffic_source_id": 836,
    }


def test_google_stream_payload_matches_contract() -> None:
    """Поток 1: country=MX (accept) → редирект на Google, позиция 1."""
    payload = build_google_stream(1001, "MX", GOOGLE_URL)

    assert payload.model_dump(by_alias=True, exclude_none=True) == {
        "campaign_id": 1001,
        "name": "Google (MX)",
        "type": "regular",
        "schema": "redirect",
        "action_type": "http",
        "action_payload": GOOGLE_URL,
        "collect_clicks": True,
        "position": 1,
        "filters": [{"name": "country", "mode": "accept", "payload": ["MX"]}],
        "offers": [],
    }


def test_offer_stream_payload_matches_contract() -> None:
    """Поток 2: без фильтров, оффер с share=100, позиция 2."""
    payload = build_offer_stream(1001, 18813)

    assert payload.model_dump(by_alias=True, exclude_none=True) == {
        "campaign_id": 1001,
        "name": "Offers",
        "type": "default",
        "schema": "landings",
        "action_type": "http",
        "action_payload": "",
        "collect_clicks": True,
        "position": 2,
        "filters": [],
        "offers": [{"offer_id": 18813, "share": 100}],
    }


def test_google_redirect_has_no_trailing_slash(settings: Settings) -> None:
    """`HttpUrl` добавляет слэш к пустому пути; в payload нужен чистый URL."""
    assert google_redirect(settings) == GOOGLE_URL


def test_alias_has_random_suffix_and_slug() -> None:
    alias = make_alias("MX test")
    assert re.fullmatch(r"mx-test-[a-z0-9]{6}", alias)

    cyrillic = make_alias("Тестовая кампания")
    assert re.fullmatch(r"campaign-[a-z0-9]{6}", cyrillic)


def test_request_normalizes_geo() -> None:
    assert CampaignRequest(name=" MX ", geo=" mx ", offer_id=18813).geo == "MX"


@pytest.mark.parametrize("geo", ["MEX", "M", "12", "МХ"])
def test_request_rejects_invalid_geo(geo: str) -> None:
    with pytest.raises(ValueError, match="ISO-2"):
        CampaignRequest(name="MX test", geo=geo, offer_id=18813)


async def test_create_campaign_creates_two_streams(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Успешный сценарий: справочники, оффер, кампания, два потока, журнал."""
    result = await create_campaign(
        session, stub, settings, request_payload, idempotency_key="key-ok"
    )

    assert result.campaign_id == 1001
    assert result.geo == "MX"
    assert result.replayed is False
    assert [stream.type for stream in result.streams] == ["regular", "default"]
    assert [stream.schema_ for stream in result.streams] == ["redirect", "landings"]
    assert stub.names == [
        "list_domains",
        "list_groups",
        "list_traffic_sources",
        "get_offer",
        "create_campaign",
        "list_campaign_streams",
        "create_stream",
        "create_stream",
    ]

    stored = await find_operation(session, "key-ok")
    assert stored is not None
    assert stored.status == OperationStatus.SUCCEEDED
    assert stored.external_id == "1001"
    assert stored.payload == {"name": "MX test", "geo": "MX", "offer_id": 18813}
    assert stored.result is not None
    assert stored.result["campaign_id"] == 1001
    assert [stream["schema"] for stream in stored.result["streams"]] == ["redirect", "landings"]


async def test_cached_directories_avoid_keitaro_calls(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Кэш шага 3 используется: повторно справочники не читаются."""
    await seed_directories(session)

    await create_campaign(session, stub, settings, request_payload, idempotency_key="key-cache")

    assert stub.names == [
        "get_offer",
        "create_campaign",
        "list_campaign_streams",
        "create_stream",
        "create_stream",
    ]


async def test_existing_default_stream_is_reused(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Keitaro мог создать default-поток сам — обновляем его, а не плодим второй."""
    stub = StubKeitaro(auto_default_stream=True)

    result = await create_campaign(session, stub, settings, request_payload, idempotency_key="k")

    assert stub.names.count("create_stream") == 1
    assert "update_stream" in stub.names
    assert result.streams[1].id == DEFAULT_STREAM_ID
    assert result.streams[1].type == "default"


async def test_failure_on_google_stream_compensates(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Сбой второго шага: кампания уходит в архив, в журнале — ошибка."""
    stub = StubKeitaro(fail_stage="google")

    with pytest.raises(KeitaroUnavailable):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="key-fail")

    assert stub.deleted == [1001]
    stored = await find_operation(session, "key-fail")
    assert stored is not None
    assert stored.status == OperationStatus.FAILED
    assert "сеть недоступна" in (stored.error or "")
    assert "вручную" not in (stored.error or "")


async def test_failed_compensation_is_reported_in_journal(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Если архив не удался, в журнале остаётся подсказка удалить вручную."""
    stub = StubKeitaro(fail_stage="google", compensate_fails=True)

    with pytest.raises(KeitaroUnavailable):
        await create_campaign(
            session, stub, settings, request_payload, idempotency_key="key-manual"
        )

    stored = await find_operation(session, "key-manual")
    assert stored is not None
    assert stored.status == OperationStatus.FAILED
    assert "удалите кампанию 1001 вручную" in (stored.error or "")


async def test_replay_returns_stored_result_without_keitaro_calls(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Двойной клик: второй запрос получает ответ из журнала, Keitaro не трогаем."""
    first = await create_campaign(session, stub, settings, request_payload, idempotency_key="key-x")
    calls_after_first = list(stub.calls)

    second = await create_campaign(
        session, stub, settings, request_payload, idempotency_key="key-x"
    )

    assert second.replayed is True
    assert second.campaign_id == first.campaign_id
    assert second.streams == first.streams
    assert stub.calls == calls_after_first
    assert await session.scalar(select(func.count()).select_from(OperationLog)) == 1


async def test_same_key_with_different_payload_conflicts(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Ключ от одной кампании нельзя переиспользовать для другой."""
    await create_campaign(session, stub, settings, request_payload, idempotency_key="key-same")

    other = CampaignRequest(name="Другая кампания", geo="PE", offer_id=18813)
    with pytest.raises(IdempotencyConflict, match="другим телом"):
        await create_campaign(session, stub, settings, other, idempotency_key="key-same")

    assert stub.names.count("create_campaign") == 1


async def test_pending_operation_conflicts(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Пока первый воркер создаёт кампанию, второй получает 409, а не дубль."""
    await claim_operation(
        session,
        idempotency_key="key-busy",
        operation_type=OperationType.CAMPAIGN_CREATE,
        payload=request_payload.model_dump(mode="json"),
    )
    await session.commit()

    with pytest.raises(IdempotencyConflict, match="ещё выполняется"):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="key-busy")

    assert "create_campaign" not in stub.names


async def test_failed_operation_cannot_be_replayed(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Повтор упавшей операции требует нового ключа и объясняет причину."""
    stub = StubKeitaro(fail_stage="google")
    with pytest.raises(KeitaroUnavailable):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="key-bad")

    with pytest.raises(IdempotencyConflict, match="завершилась ошибкой"):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="key-bad")


async def test_missing_offer_is_request_error(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """404 по офферу — это 422 «неверный offer_id», а не 404 ручки."""
    stub = StubKeitaro(offers={})

    with pytest.raises(CampaignRequestError, match="не найден в Keitaro"):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="key-offer")

    assert "create_campaign" not in stub.names
    stored = await find_operation(session, "key-offer")
    assert stored is not None and stored.status == OperationStatus.FAILED


async def test_inactive_offer_is_request_error(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    stub = StubKeitaro(offers={18813: Offer(id=18813, name="Peru", state="paused")})

    with pytest.raises(CampaignRequestError, match="не активен"):
        await create_campaign(
            session, stub, settings, request_payload, idempotency_key="key-paused"
        )

    assert "create_campaign" not in stub.names


async def test_missing_configured_domain_is_request_error(
    session: AsyncSession,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Сущность из `.env` отсутствует в Keitaro — понятная 422 до создания."""
    stub = StubKeitaro(domains=[Domain(id=1, name="other.example")])

    with pytest.raises(CampaignRequestError, match="домен с id=1813"):
        await create_campaign(
            session, stub, settings, request_payload, idempotency_key="key-domain"
        )

    # Справочник обновлялся ровно один раз
    assert stub.names.count("list_domains") == 1
    assert "create_campaign" not in stub.names


async def test_generated_key_is_stored(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    """Без заголовка сервер генерирует ключ и возвращает его клиенту."""
    result = await create_campaign(session, stub, settings, request_payload)

    assert len(result.idempotency_key) == 32
    stored = await find_operation(session, result.idempotency_key)
    assert stored is not None and stored.status == OperationStatus.SUCCEEDED


async def test_too_long_key_is_rejected_before_keitaro(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
) -> None:
    with pytest.raises(CampaignRequestError, match="длиннее 128"):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="k" * 129)

    assert stub.calls == []
    assert await session.scalar(select(func.count()).select_from(OperationLog)) == 0


async def test_journal_failure_after_creation_does_not_archive_campaign(
    session: AsyncSession,
    stub: StubKeitaro,
    settings: Settings,
    request_payload: CampaignRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Сбой записи результата не должен удалять уже созданную кампанию.

    Компенсация уместна только когда кампания не доделана. Если кампания и
    потоки созданы, а упал журнал, удаление лишило бы пользователя рабочей
    кампании; в журнал и лог пишется подсказка проверить её в Keitaro.
    """

    async def broken_mark_succeeded(*args: object, **kwargs: object) -> None:
        raise OperationalError("UPDATE operation_log", {}, Exception("db down"))

    monkeypatch.setattr("keitaro_wrapper.services.campaigns.mark_succeeded", broken_mark_succeeded)

    with pytest.raises(OperationalError):
        await create_campaign(session, stub, settings, request_payload, idempotency_key="key-db")

    assert stub.deleted == []
    stored = await find_operation(session, "key-db")
    assert stored is not None
    assert stored.status == OperationStatus.FAILED
    assert "проверьте её в Keitaro" in (stored.error or "")
