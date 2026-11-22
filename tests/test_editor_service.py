"""Тесты редактора: веса, цифры из отчёта, добавление и удаление офферов."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from stub_keitaro import (
    CAMPAIGN_ID,
    DEFAULT_STREAMS,
    OFFER_ID,
    SECOND_OFFER_ID,
    StubKeitaro,
)

from keitaro_wrapper.db import OperationLog, OperationStatus
from keitaro_wrapper.db.directories import DirectoryEntry, list_directory, replace_directory
from keitaro_wrapper.db.operations import find_operation
from keitaro_wrapper.keitaro import KeitaroUnavailable, Offer, ReportRow, Stream, StreamOffer
from keitaro_wrapper.services.editor import (
    AddOfferRequest,
    add_offer,
    distribute_shares,
    get_campaign_detail,
    list_campaigns,
    list_offers,
    remove_offer,
)
from keitaro_wrapper.services.errors import Conflict, NotFound, RequestError


@pytest.fixture
def stub() -> StubKeitaro:
    """Двойник с готовыми потоками кампании 1001."""
    return StubKeitaro(streams=DEFAULT_STREAMS)


def stream_with(
    offers: list[tuple[int, int]],
    *,
    stream_id: int = 2,
    stream_type: str = "default",
    **kwargs: object,
) -> Stream:
    return Stream(
        id=stream_id,
        name="Offers",
        type=stream_type,
        campaign_id=CAMPAIGN_ID,
        schema_="landings",
        action_type="http",
        position=2,
        weight=100,
        offers=[
            StreamOffer(offer_id=offer_id, share=share, state="active")
            for offer_id, share in offers
        ],
        **kwargs,
    )


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, [100]), (2, [50, 50]), (3, [34, 33, 33]), (7, [15, 15, 14, 14, 14, 14, 14])],
)
def test_distribute_shares_sums_to_100(count: int, expected: list[int]) -> None:
    shares = distribute_shares(count)
    assert shares == expected
    assert sum(shares) == 100


def test_distribute_shares_of_nothing() -> None:
    assert distribute_shares(0) == []


async def test_list_campaigns_returns_sorted_and_caches(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    stub.campaigns = list(reversed(stub.campaigns))

    campaigns = await list_campaigns(session, stub)

    assert [campaign.id for campaign in campaigns] == [CAMPAIGN_ID]
    cached = await list_directory(session, "campaigns")
    assert [(row.external_id, row.name) for row in cached] == [(str(CAMPAIGN_ID), "MX test")]


async def test_list_offers_returns_sorted_and_caches(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    offers = await list_offers(session, stub)

    assert [offer.id for offer in offers] == [OFFER_ID, SECOND_OFFER_ID]
    assert offers[0].country == ["Peru"]
    cached = await list_directory(session, "offers")
    assert len(cached) == 2


async def test_detail_maps_report_rows_to_offers(session: AsyncSession, stub: StubKeitaro) -> None:
    """Цифры: строки отчёта по кампании, включая клики без оффера в totals."""
    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    assert detail.name == "MX test"
    assert [stream.type for stream in detail.streams] == ["regular", "default"]
    offer = detail.streams[1].offers[0]
    assert (offer.offer_id, offer.name, offer.share) == (OFFER_ID, "Peru", 100)
    assert (offer.clicks, offer.conversions) == (10, 2)
    # 5 кликов редиректа (offer=null) + 10 кликов оффера
    assert (detail.clicks, detail.conversions) == (15, 2)
    assert detail.stats_available is True


async def test_detail_passes_interval_to_report(session: AsyncSession, stub: StubKeitaro) -> None:
    await get_campaign_detail(session, stub, CAMPAIGN_ID, interval="7_days_ago")

    report_call = next(payload for name, payload in stub.calls if name == "build_report")
    assert report_call.range == {"interval": "7_days_ago"}
    # id-измерения: цифры привязываются к потоку и офферу, а не к именам
    assert report_call.dimensions == ["campaign_id", "stream_id", "offer_id"]
    # Отчёт сужается фильтром по кампании: фильтр проверен живой пробой 21.11.2026
    assert report_call.filters == [
        {"name": "campaign_id", "operator": "EQUALS", "expression": CAMPAIGN_ID}
    ]


async def test_detail_numbers_are_split_by_stream(session: AsyncSession, stub: StubKeitaro) -> None:
    """Один оффер в двух потоках: цифры каждого потока — свои."""
    stub.streams = [
        stream_with([(OFFER_ID, 100)], stream_id=1, stream_type="regular"),
        stream_with([(OFFER_ID, 100)], stream_id=2),
    ]
    stub.report_rows = [
        ReportRow(campaign_id=CAMPAIGN_ID, stream_id=1, offer_id=OFFER_ID, clicks=7, conversions=1),
        ReportRow(campaign_id=CAMPAIGN_ID, stream_id=2, offer_id=OFFER_ID, clicks=3, conversions=0),
    ]

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    assert [stream.offers[0].clicks for stream in detail.streams] == [7, 3]
    assert [stream.offers[0].conversions for stream in detail.streams] == [1, 0]
    assert (detail.clicks, detail.conversions) == (10, 1)


async def test_detail_does_not_merge_same_named_offers(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Одинаковые имена офферов не складываются: цифры считаются по `offer_id`."""
    stub.offers[18815] = Offer(id=18815, name="Peru", state="active")
    stub.streams = [stream_with([(OFFER_ID, 50), (18815, 50)])]
    stub.report_rows = [
        ReportRow(campaign_id=CAMPAIGN_ID, stream_id=2, offer_id=OFFER_ID, clicks=4, conversions=0),
        ReportRow(campaign_id=CAMPAIGN_ID, stream_id=2, offer_id=18815, clicks=9, conversions=1),
    ]

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    clicks = {offer.offer_id: offer.clicks for offer in detail.streams[0].offers}
    assert clicks == {OFFER_ID: 4, 18815: 9}
    assert (detail.clicks, detail.conversions) == (13, 1)


async def test_detail_hides_numbers_when_report_has_no_campaign_id(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Строки без `campaign_id` — не нули, а честный «цифры недоступны».

    До фикса такие строки просто не проходили фильтр по id, и редактор
    показывал `0` кликов при `stats_available=True`, то есть молча врал.
    """
    stub.report_rows = [
        ReportRow(offer=None, clicks=10, conversions=1),
        ReportRow(offer="Peru", clicks=5, conversions=2),
    ]

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    assert detail.stats_available is False
    assert (detail.clicks, detail.conversions) == (0, 0)
    assert detail.streams[1].offers[0].clicks == 0


async def test_detail_does_not_merge_same_named_campaigns(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Отчёт фильтруется по campaign_id: одинаковые имена не складывают цифры."""
    stub.report_rows = [
        ReportRow(campaign_id=CAMPAIGN_ID, stream_id=2, offer_id=OFFER_ID, clicks=1, conversions=0),
        ReportRow(campaign_id=999, stream_id=2, offer_id=OFFER_ID, clicks=100, conversions=50),
    ]

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    assert (detail.clicks, detail.conversions) == (1, 0)
    assert detail.streams[1].offers[0].clicks == 1


async def test_detail_survives_broken_report(session: AsyncSession, stub: StubKeitaro) -> None:
    """Упавший отчёт не должен делать редактор недоступным."""
    stub.report_fails = True

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    assert detail.stats_available is False
    assert (detail.clicks, detail.conversions) == (0, 0)
    assert detail.streams[1].offers[0].clicks == 0


async def test_detail_uses_cached_offer_names(session: AsyncSession, stub: StubKeitaro) -> None:
    await replace_directory(
        session,
        kind="offers",
        entries=[DirectoryEntry(external_id=str(OFFER_ID), name="Peru", payload={"id": OFFER_ID})],
    )
    await session.commit()

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    assert detail.streams[1].offers[0].name == "Peru"
    assert "list_offers" not in stub.names


async def test_detail_fetches_missing_offer_name(session: AsyncSession, stub: StubKeitaro) -> None:
    """Кэш заполнен частично: недостающий оффер читается по одному."""
    await replace_directory(
        session,
        kind="offers",
        entries=[DirectoryEntry(external_id=str(OFFER_ID), name="Peru", payload={"id": OFFER_ID})],
    )
    await session.commit()
    stub.streams = [stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)])]

    detail = await get_campaign_detail(session, stub, CAMPAIGN_ID)

    names = [offer.name for offer in detail.streams[0].offers]
    assert names == ["Peru", "Second"]
    assert "list_offers" not in stub.names
    assert stub.names.count("get_offer") == 1


async def test_add_offer_rebalances_weights_to_100(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    result = await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID),
        idempotency_key="add-1",
    )

    assert result.stream_id == 2
    assert [(offer.offer_id, offer.share, offer.position) for offer in result.offers] == [
        (OFFER_ID, 50, 1),
        (SECOND_OFFER_ID, 50, 2),
    ]

    update_name, (stream_id, payload) = next(
        call for call in stub.calls if call[0] == "update_stream"
    )
    assert update_name == "update_stream"
    assert stream_id == 2
    # PUT отправляет полный поток: поля, которые редактор не меняет, сохранены
    body = payload.model_dump(by_alias=True, exclude_none=True)
    assert body["campaign_id"] == CAMPAIGN_ID
    assert body["name"] == "Offers"
    assert body["type"] == "default"
    assert body["schema"] == "landings"
    assert body["action_type"] == "http"
    assert body["position"] == 2
    assert body["weight"] == 100
    assert body["offers"] == [
        {"offer_id": OFFER_ID, "share": 50, "state": "active"},
        {"offer_id": SECOND_OFFER_ID, "share": 50},
    ]

    stored = await find_operation(session, "add-1")
    assert stored is not None and stored.status == OperationStatus.SUCCEEDED
    assert stored.external_id == "2"
    assert stored.result["offers"][0]["share"] == 50


async def test_add_offer_preserves_triggers_and_landings(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """PUT полный: настройки потока, которые редактор не трогает, не теряются."""
    stub.streams = [
        stream_with(
            [(OFFER_ID, 100)],
            triggers=[{"name": "conversion"}],
            landings=[{"landing_id": 5, "share": 100}],
            comments="важный поток",
            filter_or=True,
            offer_selection="before_click",
        )
    ]

    await add_offer(session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=SECOND_OFFER_ID))

    _, (_, payload) = next(call for call in stub.calls if call[0] == "update_stream")
    body = payload.model_dump(by_alias=True, exclude_none=True)
    assert body["triggers"] == [{"name": "conversion"}]
    assert body["landings"] == [{"landing_id": 5, "share": 100}]
    assert body["comments"] == "важный поток"
    assert body["filter_or"] is True
    assert body["offer_selection"] == "before_click"


async def test_add_third_offer_gives_34_33_33(session: AsyncSession, stub: StubKeitaro) -> None:
    stub.streams = [stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)])]
    stub.offers[18815] = Offer(id=18815, name="Third", state="active")

    result = await add_offer(session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=18815))

    assert [offer.share for offer in result.offers] == [34, 33, 33]
    assert sum(offer.share for offer in result.offers) == 100


async def test_add_offer_targets_given_stream(session: AsyncSession, stub: StubKeitaro) -> None:
    """Оффер добавляется в выбранный поток, а не «куда-нибудь».

    До фикса добавление адресовалось кампании: при пустом `default`-потоке оффер
    уходил в него (пользователь видел результат в другом потоке), а при двух
    потоках с офферами добавление вообще отказывало с 422.
    """
    stub.streams = [
        stream_with([], stream_id=2),
        stream_with([(OFFER_ID, 100)], stream_id=3, stream_type="regular"),
    ]

    result = await add_offer(
        session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=SECOND_OFFER_ID, stream_id=3)
    )

    assert result.stream_id == 3
    assert [(offer.offer_id, offer.share) for offer in result.offers] == [
        (OFFER_ID, 50),
        (SECOND_OFFER_ID, 50),
    ]
    remaining = {stream.id: [offer.offer_id for offer in stream.offers] for stream in stub.streams}
    # Пустой default-поток не тронут: оффер попал туда, куда просили.
    assert remaining[2] == []
    assert remaining[3] == [OFFER_ID, SECOND_OFFER_ID]


async def test_add_offer_without_stream_is_ambiguous(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Без `stream_id` при двух потоках с офферами API просит выбрать поток."""
    stub.streams = [
        stream_with([(OFFER_ID, 100)], stream_id=1, stream_type="regular"),
        stream_with([(SECOND_OFFER_ID, 100)], stream_id=2, stream_type="regular"),
    ]

    with pytest.raises(RequestError, match="выберите поток"):
        await add_offer(session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=OFFER_ID))

    assert "update_stream" not in stub.names


async def test_add_offer_with_unknown_stream_returns_404(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Поток могли удалить в Keitaro: 404 с объяснением, без PUT."""
    stub.streams = [stream_with([(OFFER_ID, 100)])]

    with pytest.raises(NotFound, match="нет потока"):
        await add_offer(
            session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=SECOND_OFFER_ID, stream_id=999)
        )

    assert "update_stream" not in stub.names


async def test_add_same_key_for_two_streams_conflicts(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Тело операции содержит `stream_id`: другой поток с тем же ключом — 409."""
    stub.streams = [
        stream_with([(OFFER_ID, 100)], stream_id=1, stream_type="regular"),
        stream_with([(OFFER_ID, 100)], stream_id=2),
    ]
    await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID, stream_id=1),
        idempotency_key="add-two",
    )

    with pytest.raises(Conflict, match="другим телом"):
        await add_offer(
            session,
            stub,
            CAMPAIGN_ID,
            AddOfferRequest(offer_id=SECOND_OFFER_ID, stream_id=2),
            idempotency_key="add-two",
        )


async def test_add_offer_without_stream_prefers_stream_with_offers(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Без `stream_id` оффер идёт туда, где уже лежат офферы, а не в пустой default.

    До фикса `default`-поток был первым в приоритете даже пустым, и добавление
    через API уходило в поток, которого пользователь не видел.
    """
    stub.streams = [
        stream_with([], stream_id=1),
        stream_with([(OFFER_ID, 100)], stream_id=3, stream_type="regular"),
    ]

    result = await add_offer(session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=SECOND_OFFER_ID))

    assert result.stream_id == 3
    remaining = {stream.id: [offer.offer_id for offer in stream.offers] for stream in stub.streams}
    assert remaining[1] == []
    assert remaining[3] == [OFFER_ID, SECOND_OFFER_ID]


async def test_add_offer_legacy_payload_replays(session: AsyncSession, stub: StubKeitaro) -> None:
    """Тело операции без `stream_id` должно повторяться, а не давать 409.

    Пока поток не задан, поля `stream_id` в payload быть не должно: иначе записи
    журнала, сделанные до появления этого поля, перестают совпадать по телу и
    повтор превращается в 409 «другим телом запроса» вместо `replayed: true`.
    """
    first = await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID),
        idempotency_key="legacy-add",
    )
    stored = await find_operation(session, "legacy-add")
    assert stored is not None
    assert stored.payload == {"campaign_id": CAMPAIGN_ID, "offer_id": SECOND_OFFER_ID}

    calls_before = list(stub.calls)
    second = await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID),
        idempotency_key="legacy-add",
    )

    assert second.replayed is True
    assert second.offers == first.offers
    assert stub.calls == calls_before


async def test_add_offer_with_stream_keeps_it_in_payload(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Если поток задан — он в payload: повтор с другим потоком это другая операция."""
    await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID, stream_id=2),
        idempotency_key="add-with-stream",
    )

    stored = await find_operation(session, "add-with-stream")
    assert stored is not None
    assert stored.payload == {
        "campaign_id": CAMPAIGN_ID,
        "offer_id": SECOND_OFFER_ID,
        "stream_id": 2,
    }


async def test_add_existing_offer_conflicts(session: AsyncSession, stub: StubKeitaro) -> None:
    with pytest.raises(Conflict, match="уже есть"):
        await add_offer(
            session,
            stub,
            CAMPAIGN_ID,
            AddOfferRequest(offer_id=OFFER_ID),
            idempotency_key="dup-offer",
        )

    assert "update_stream" not in stub.names
    stored = await find_operation(session, "dup-offer")
    assert stored is not None and stored.status == OperationStatus.FAILED


async def test_add_offer_replay_does_not_touch_keitaro(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    first = await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID),
        idempotency_key="add-replay",
    )
    calls_after_first = list(stub.calls)

    second = await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID),
        idempotency_key="add-replay",
    )

    assert second.replayed is True
    assert second.offers == first.offers
    assert stub.calls == calls_after_first
    assert len((await session.scalars(select(OperationLog))).all()) == 1


async def test_add_inactive_offer_is_rejected(session: AsyncSession, stub: StubKeitaro) -> None:
    stub.offers[SECOND_OFFER_ID] = Offer(id=SECOND_OFFER_ID, name="Second", state="paused")

    with pytest.raises(RequestError, match="не активен"):
        await add_offer(session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=SECOND_OFFER_ID))

    assert "update_stream" not in stub.names


async def test_remove_offer_rebalances_remaining(session: AsyncSession, stub: StubKeitaro) -> None:
    stub.streams = [stream_with([(OFFER_ID, 34), (SECOND_OFFER_ID, 33), (18815, 33)])]

    result = await remove_offer(session, stub, CAMPAIGN_ID, SECOND_OFFER_ID)

    assert [(offer.offer_id, offer.share) for offer in result.offers] == [
        (OFFER_ID, 50),
        (18815, 50),
    ]
    assert sum(offer.share for offer in result.offers) == 100


async def test_remove_last_offer_is_rejected(session: AsyncSession, stub: StubKeitaro) -> None:
    with pytest.raises(RequestError, match="последний оффер"):
        await remove_offer(session, stub, CAMPAIGN_ID, OFFER_ID)

    assert "update_stream" not in stub.names


async def test_remove_missing_offer_returns_404(session: AsyncSession, stub: StubKeitaro) -> None:
    with pytest.raises(NotFound, match="отсутствует в потоке"):
        await remove_offer(session, stub, CAMPAIGN_ID, SECOND_OFFER_ID)

    assert "update_stream" not in stub.names


async def test_remove_offer_replay_is_idempotent(session: AsyncSession, stub: StubKeitaro) -> None:
    stub.streams = [stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)])]
    first = await remove_offer(
        session, stub, CAMPAIGN_ID, SECOND_OFFER_ID, idempotency_key="remove-1"
    )
    calls_after_first = list(stub.calls)

    second = await remove_offer(
        session, stub, CAMPAIGN_ID, SECOND_OFFER_ID, idempotency_key="remove-1"
    )

    assert second.replayed is True
    assert second.offers == first.offers
    assert stub.calls == calls_after_first
    assert stub.names.count("update_stream") == 1


async def test_remove_without_default_uses_single_offer_stream(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """На живом инстансе офферы бывают в regular-потоке — редактор их находит."""
    stub.streams = [stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)], stream_type="regular")]

    result = await remove_offer(session, stub, CAMPAIGN_ID, SECOND_OFFER_ID)

    assert result.stream_id == 2
    assert [offer.share for offer in result.offers] == [100]


async def test_remove_offer_targets_given_stream(session: AsyncSession, stub: StubKeitaro) -> None:
    """Один оффер в двух потоках: удаление адресуется конкретному потоку.

    Без `stream_id` редактор берёт `default`-поток, поэтому удалить оффер из
    другого потока было нельзя, а повтор с тем же ключом молча возвращал
    «удалено», хотя в этом потоке оффер оставался.
    """
    stub.streams = [
        stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)], stream_id=1, stream_type="regular"),
        stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)], stream_id=2),
    ]

    result = await remove_offer(session, stub, CAMPAIGN_ID, OFFER_ID, stream_id=1)

    assert result.stream_id == 1
    assert [(offer.offer_id, offer.share) for offer in result.offers] == [(SECOND_OFFER_ID, 100)]
    remaining = {stream.id: [offer.offer_id for offer in stream.offers] for stream in stub.streams}
    assert remaining[1] == [SECOND_OFFER_ID]
    # Второй поток не тронут: там тот же оффер со своей долей.
    assert remaining[2] == [OFFER_ID, SECOND_OFFER_ID]


async def test_same_key_for_two_streams_conflicts(session: AsyncSession, stub: StubKeitaro) -> None:
    """Ключ от удаления в одном потоке нельзя переиспользовать для другого.

    Тело операции содержит `stream_id`, поэтому повтор с тем же ключом для
    другого потока — это другая операция: честный 409, а не «удалено».
    """
    stub.streams = [
        stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)], stream_id=1, stream_type="regular"),
        stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)], stream_id=2),
    ]
    await remove_offer(session, stub, CAMPAIGN_ID, OFFER_ID, stream_id=1, idempotency_key="two")

    with pytest.raises(Conflict, match="другим телом"):
        await remove_offer(session, stub, CAMPAIGN_ID, OFFER_ID, stream_id=2, idempotency_key="two")


async def test_remove_offer_with_unknown_stream_returns_404(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    """Поток могли удалить в Keitaro: 404 с объяснением, без PUT."""
    stub.streams = [stream_with([(OFFER_ID, 50), (SECOND_OFFER_ID, 50)])]

    with pytest.raises(NotFound, match="нет потока"):
        await remove_offer(session, stub, CAMPAIGN_ID, OFFER_ID, stream_id=999)

    assert "update_stream" not in stub.names


async def test_no_offer_stream_is_request_error(session: AsyncSession, stub: StubKeitaro) -> None:
    stub.streams = []

    with pytest.raises(RequestError, match="нет потока с офферами"):
        await add_offer(session, stub, CAMPAIGN_ID, AddOfferRequest(offer_id=SECOND_OFFER_ID))


async def test_update_failure_marks_operation_failed(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    stub.fail_stage = "update"

    with pytest.raises(KeitaroUnavailable):
        await add_offer(
            session,
            stub,
            CAMPAIGN_ID,
            AddOfferRequest(offer_id=SECOND_OFFER_ID),
            idempotency_key="put-fail",
        )

    stored = await find_operation(session, "put-fail")
    assert stored is not None and stored.status == OperationStatus.FAILED
    # Поток не изменился: PUT завершился ошибкой
    offers_stream = next(stream for stream in stub.streams if stream.id == 2)
    assert [offer.share for offer in offers_stream.offers] == [100]


async def test_same_key_for_add_and_remove_conflicts(
    session: AsyncSession, stub: StubKeitaro
) -> None:
    await add_offer(
        session,
        stub,
        CAMPAIGN_ID,
        AddOfferRequest(offer_id=SECOND_OFFER_ID),
        idempotency_key="shared",
    )

    with pytest.raises(Conflict, match="другим телом"):
        await remove_offer(session, stub, CAMPAIGN_ID, SECOND_OFFER_ID, idempotency_key="shared")
