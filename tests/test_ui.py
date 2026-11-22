"""Тесты HTML-интерфейса: рендер страниц, селекты из кэша и действия формами."""

import logging
import re
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
from keitaro_wrapper.db.directories import list_directory
from keitaro_wrapper.keitaro import Campaign, Offer, ReportRow, Stream, StreamOffer
from keitaro_wrapper.main import create_app

ENV = {
    "KEITARO_BASE_URL": "https://example.keitaro.io",
    "KEITARO_API_KEY": "test-key",
    "KEITARO_DOMAIN_ID": "1813",
    "KEITARO_GROUP_ID": "10332",
    "KEITARO_SOURCE_ID": "836",
}
CREATOR_FORM = {
    "name": "UI campaign",
    "geo": "MX",
    "offer_id": str(OFFER_ID),
    "domain_id": "1813",
    "group_id": "10332",
    "traffic_source_id": "836",
    "idempotency_key": "ui-create",
}


@pytest.fixture
def ui_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def stub() -> StubKeitaro:
    return StubKeitaro(streams=DEFAULT_STREAMS)


@pytest.fixture
async def ui_client(
    db_engine: AsyncEngine, stub: StubKeitaro, ui_env: None
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP-клиент поверх приложения с тестовой БД и двойником Keitaro."""
    app = create_app()
    app.state.session_factory = create_session_factory(db_engine)
    app.state.keitaro = stub
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        yield client


def keys_in(html: str) -> list[str]:
    """Все скрытые ключи идемпотентности страницы (по одному на действие)."""
    return re.findall(r'name="idempotency_key" value="([0-9a-f]{32})"', html)


DELETE_FORM = re.compile(
    r'action="/ui/editor/\d+/offers/(?P<offer>\d+)/delete"(?P<body>.*?)</form>', re.S
)


def delete_forms(html: str) -> list[dict[str, str]]:
    """Скрытые поля каждой формы удаления: `stream_id` и `idempotency_key`."""
    forms = []
    for match in DELETE_FORM.finditer(html):
        fields = dict(re.findall(r'name="(\w+)" value="([^"]*)"', match.group("body")))
        forms.append({"offer_id": match.group("offer"), **fields})
    return forms


ADD_FORM = re.compile(r'action="/ui/editor/\d+/offers"(?P<body>.*?)</form>', re.S)


def add_form(html: str) -> str:
    """Тело формы добавления оффера (селекты потоков и офферов, скрытые ключи)."""
    match = ADD_FORM.search(html)
    return match.group("body") if match else ""


async def journal(db_engine: AsyncEngine) -> list[OperationLog]:
    factory = create_session_factory(db_engine)
    async with factory() as session:
        return list(await session.scalars(select(OperationLog)))


async def test_root_redirects_to_creator(ui_client: httpx.AsyncClient) -> None:
    response = await ui_client.get("/")

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/creator"


async def test_creator_page_renders_options_from_cache(
    ui_client: httpx.AsyncClient, stub: StubKeitaro, db_engine: AsyncEngine
) -> None:
    response = await ui_client.get("/ui/creator")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    html = response.text
    assert "Создатель кампании" in html
    assert "example.com" in html
    assert "Campaigns" in html
    assert "Google" in html
    assert "Peru" in html
    assert "Second" in html
    assert 'value="1813" selected' in html
    assert len(keys_in(html)) == 1
    # Первый заход заполнил кэш всеми четырьмя справочниками
    factory = create_session_factory(db_engine)
    async with factory() as session:
        for kind in ("domains", "groups", "traffic_sources", "offers"):
            assert await list_directory(session, kind)
    assert {
        "list_domains",
        "list_groups",
        "list_traffic_sources",
        "list_offers",
    } <= set(stub.names)


async def test_creator_without_active_offers_disables_submit(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.offers = {OFFER_ID: Offer(id=OFFER_ID, name="Paused", state="paused")}

    response = await ui_client.get("/ui/creator")

    assert response.status_code == 200
    assert "нет активных офферов" in response.text
    assert 'type="submit" disabled' in response.text


async def test_second_creator_render_does_not_hit_keitaro(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Второй рендер берёт справочники из кэша и всё равно рисует селекты.

    Одного «Keitaro не вызывали» мало: тест должен падать, если справочники
    вернут пустоту, поэтому проверяем и содержимое селектов.
    """
    first = await ui_client.get("/ui/creator")
    calls_after_first = list(stub.calls)

    response = await ui_client.get("/ui/creator")

    assert response.status_code == 200
    assert stub.calls == calls_after_first
    html = response.text
    assert html.count("<option") == first.text.count("<option")
    for expected in ("example.com", "Campaigns", "Google", "Peru", "Second"):
        assert expected in html


async def test_creator_submit_creates_campaign_and_redirects(
    ui_client: httpx.AsyncClient, stub: StubKeitaro, db_engine: AsyncEngine
) -> None:
    response = await ui_client.post("/ui/creator", data=CREATOR_FORM)

    assert response.status_code == 303
    assert response.headers["location"] == f"/ui/editor?campaign_id={CAMPAIGN_ID}&created=1"
    create_calls = [payload for name, payload in stub.calls if name == "create_campaign"]
    assert len(create_calls) == 1
    assert create_calls[0].domain_id == 1813
    assert create_calls[0].group_id == 10332
    assert create_calls[0].traffic_source_id == 836
    stored = await journal(db_engine)
    assert len(stored) == 1
    assert stored[0].status == OperationStatus.SUCCEEDED

    page = await ui_client.get(response.headers["location"])
    assert page.status_code == 200
    assert "Кампания создана" in page.text
    assert "UI campaign" in page.text


async def test_creator_submit_uses_selected_directory_ids(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    data = {**CREATOR_FORM, "domain_id": "1813", "group_id": "10332", "traffic_source_id": "836"}

    response = await ui_client.post("/ui/creator", data=data)

    assert response.status_code == 303
    sent = next(payload for name, payload in stub.calls if name == "create_campaign")
    assert (sent.domain_id, sent.group_id, sent.traffic_source_id) == (1813, 10332, 836)


async def test_creator_validation_error_renders_form(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post("/ui/creator", data={**CREATOR_FORM, "geo": "MEX"})

    assert response.status_code == 422
    assert "ISO-2" in response.text
    assert 'value="MEX"' in response.text
    assert "create_campaign" not in stub.names
    assert "ID справочника" not in response.text
    assert len(keys_in(response.text)) == 1
    assert 'value="ui-create"' not in response.text


async def test_creator_blank_offer_returns_422(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post("/ui/creator", data={**CREATOR_FORM, "offer_id": ""})

    assert response.status_code == 422
    assert "выберите оффер" in response.text
    assert "create_campaign" not in stub.names


async def test_creator_unknown_offer_returns_422(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post("/ui/creator", data={**CREATOR_FORM, "offer_id": "999999"})

    assert response.status_code == 422
    assert "не найден" in response.text
    assert "create_campaign" not in stub.names


async def test_creator_keitaro_failure_returns_502(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.fail_stage = "campaign"

    response = await ui_client.post("/ui/creator", data=CREATOR_FORM)

    assert response.status_code == 502
    assert "Keitaro недоступен" in response.text
    assert "UI campaign" in response.text  # значения формы не потеряны


async def test_creator_duplicate_name_shows_human_text(
    ui_client: httpx.AsyncClient, stub: StubKeitaro, caplog: pytest.LogCaptureFixture
) -> None:
    """Занятое имя: страница объясняет причину, а метод и путь уходят в лог.

    До фикса в HTML попадал весь текст клиента —
    «POST /campaigns -> HTTP 422: name: Name has already used»: пользователь
    видел внутренний запрос вместо подсказки.
    """
    stub.fail_stage = "campaign_name_taken"

    with caplog.at_level(logging.INFO, logger="keitaro_wrapper.api.routes.ui"):
        response = await ui_client.post("/ui/creator", data=CREATOR_FORM)

    assert response.status_code == 422
    assert "уже занято" in response.text
    assert "POST /campaigns" not in response.text
    assert "HTTP 422" not in response.text
    # Значения формы не потеряны: имя можно поправить и отправить снова.
    assert CREATOR_FORM["name"] in response.text
    # Детали (метод, путь, ответ Keitaro) остались в логе приложения.
    assert any("POST /campaigns" in record.getMessage() for record in caplog.records)


async def test_creator_bad_directory_id_returns_422(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post(
        "/ui/creator", data={**CREATOR_FORM, "domain_id": "not-a-number"}
    )

    assert response.status_code == 422
    assert "должен быть числом" in response.text
    assert "create_campaign" not in stub.names


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


def mixed_streams() -> list[Stream]:
    """Пустой `default`-поток и `regular` с оффером: раньше форма предвыбирала default."""
    return [
        Stream(
            id=1,
            name="Default",
            type="default",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=1,
        ),
        Stream(
            id=3,
            name="Geo offers",
            type="regular",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=3,
            offers=[StreamOffer(offer_id=OFFER_ID, share=100, state="active")],
        ),
    ]


def shared_offer_streams() -> list[Stream]:
    """Один и тот же оффер в двух потоках: у каждого действия свой ключ."""
    offers = [
        StreamOffer(offer_id=OFFER_ID, share=50, state="active"),
        StreamOffer(offer_id=SECOND_OFFER_ID, share=50, state="active"),
    ]
    return [
        Stream(
            id=1,
            name="Google (MX)",
            type="regular",
            campaign_id=CAMPAIGN_ID,
            schema_="redirect",
            action_type="http",
            position=1,
            offers=list(offers),
        ),
        Stream(
            id=2,
            name="Offers",
            type="default",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=2,
            offers=list(offers),
        ),
    ]


async def test_editor_page_shows_streams_offers_and_numbers(
    ui_client: httpx.AsyncClient,
) -> None:
    response = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    assert response.status_code == 200
    html = response.text
    assert "Редактор офферов" in html
    assert "MX test" in html
    assert "Offers" in html
    assert "Peru" in html
    assert "id 18813" in html
    assert "100%" in html
    assert "Цифры временно недоступны" not in html
    # Ключи: селектор кампании + add + delete
    assert len(keys_in(html)) == 2


async def test_editor_empty_state(ui_client: httpx.AsyncClient, stub: StubKeitaro) -> None:
    stub.campaigns = []

    response = await ui_client.get("/ui/editor")

    assert response.status_code == 200
    assert "Кампаний пока нет" in response.text
    assert "get_campaign" not in stub.names


async def test_editor_missing_campaign_returns_404_page(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.campaigns = []

    response = await ui_client.get("/ui/editor?campaign_id=999999")

    assert response.status_code == 404
    assert "не найдено" in response.text
    assert "Кампаний пока нет" not in response.text


async def test_editor_tolerates_bad_query_params(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Мусор в query-строке — HTML-страница, а не JSON-422 от FastAPI."""
    for url in (
        "/ui/editor?interval=bogus",
        "/ui/editor?campaign_id=abc",
        "/ui/editor?campaign_id=0",
        "/ui/editor?refresh=x",
        "/ui/editor?created=x",
    ):
        response = await ui_client.get(url)

        assert response.status_code == 200, url
        assert response.headers["content-type"].startswith("text/html"), url
        assert "<select" in response.text, url


async def test_editor_banner_explains_missing_campaign_id(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Баннер объясняет и «отчёт не ответил», и «строки без привязки к кампании»."""
    stub.report_rows = [ReportRow(offer="Peru", clicks=5, conversions=1)]

    response = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    assert response.status_code == 200
    assert "Цифры временно недоступны" in response.text
    assert "без" in response.text and "привязки к кампании" in response.text


async def test_editor_report_failure_shows_banner(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.report_fails = True

    response = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    assert response.status_code == 200
    assert "Цифры временно недоступны" in response.text
    # Предупреждение должно быть стилизовано: до фикса правила `.alert-warn`
    # в `base.html` не было, и баннер выглядел как обычный текст.
    assert 'class="alert alert-warn"' in response.text
    assert ".alert-warn {" in response.text


async def test_editor_adds_offer_and_redirects(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers",
        data={
            "offer_id": str(SECOND_OFFER_ID),
            "idempotency_key": "ui-add",
            "interval": "all_time",
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/ui/editor?campaign_id={CAMPAIGN_ID}&action=added&interval=all_time"
    )
    assert "update_stream" in stub.names

    page = await ui_client.get(response.headers["location"])
    assert page.status_code == 200
    assert "Оффер добавлен" in page.text
    assert "50%" in page.text


async def test_editor_add_form_offers_stream_choice(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Форма добавления выбирает поток: по умолчанию — `default` кампании.

    До фикса поток выбирал сервис, и на кампании с пустым `default`-потоком
    оффер уходил не в тот поток, который пользователь видел на странице.
    """
    # Один оффер в двух потоках: второй оффер свободен и доступен в форме.
    stub.streams = shared_offer_streams()
    stub.streams[0].offers = [StreamOffer(offer_id=OFFER_ID, share=100, state="active")]
    stub.streams[1].offers = [StreamOffer(offer_id=OFFER_ID, share=100, state="active")]

    page = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    assert page.status_code == 200
    assert 'name="stream_id"' in page.text
    # Предвыбран default-поток (id=2), а не первый по порядку (id=1).
    assert re.search(r'<option value="2"\s+selected', page.text)
    assert not re.search(r'<option value="1"\s+selected', page.text)


async def test_editor_add_form_preselects_non_empty_stream(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Предвыбран поток, где реально лежат офферы, а не пустой `default`.

    До фикса форма предвыбирала `default`-поток даже пустым, и «Добавить»
    отправляло оффер в поток, которого пользователь не видел.
    """
    stub.streams = mixed_streams()

    page = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    body = add_form(page.text)
    assert body, "форма добавления должна быть на странице"
    assert re.search(r'<option value="3"\s+selected', body)
    assert not re.search(r'<option value="1"\s+selected', body)


async def test_editor_add_form_has_no_preselect_when_ambiguous(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Два непустых потока без `default`: выбор остаётся человеку."""
    stub.streams = [
        Stream(
            id=1,
            name="Offers A",
            type="regular",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=1,
            offers=[StreamOffer(offer_id=OFFER_ID, share=100, state="active")],
        ),
        Stream(
            id=3,
            name="Offers B",
            type="regular",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=3,
            offers=[StreamOffer(offer_id=SECOND_OFFER_ID, share=100, state="active")],
        ),
    ]
    stub.offers[18815] = Offer(id=18815, name="Third", state="active")

    page = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    body = add_form(page.text)
    assert "— выберите поток —" in body
    assert not re.search(r'<option value="\d+"\s+selected', body)


async def test_editor_add_form_lists_only_offer_streams(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Redirect-поток в селекте потоков не показывается: офферам там не место."""
    page = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    body = add_form(page.text)
    assert 'value="2"' in body  # default/landings, куда оффер и добавляется
    assert 'value="1"' not in body  # regular/redirect


async def test_editor_adds_offer_to_selected_stream(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """POST с `stream_id` меняет именно этот поток, а не default."""
    stub.streams = [
        Stream(
            id=1,
            name="Geo offers",
            type="regular",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=1,
            offers=[StreamOffer(offer_id=OFFER_ID, share=100, state="active")],
        ),
        Stream(
            id=2,
            name="Offers",
            type="default",
            campaign_id=CAMPAIGN_ID,
            schema_="landings",
            action_type="http",
            position=2,
        ),
    ]

    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers",
        data={
            "offer_id": str(SECOND_OFFER_ID),
            "stream_id": "1",
            "idempotency_key": "ui-add-stream",
            "interval": "all_time",
        },
    )

    assert response.status_code == 303
    _, (stream_id, _payload) = next(call for call in stub.calls if call[0] == "update_stream")
    assert stream_id == 1


async def test_editor_add_with_bad_stream_returns_422(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """Испорченный `stream_id` — страница с подсказкой, без обращений к Keitaro."""
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers",
        data={
            "offer_id": str(SECOND_OFFER_ID),
            "stream_id": "abc",
            "idempotency_key": "ui-bad-stream",
        },
    )

    assert response.status_code == 422
    assert "некорректный поток" in response.text
    assert "update_stream" not in stub.names


async def test_editor_duplicate_offer_returns_409_page(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers",
        data={"offer_id": str(OFFER_ID), "idempotency_key": "ui-dup"},
    )

    assert response.status_code == 409
    assert "уже есть" in response.text
    assert "update_stream" not in stub.names


async def test_editor_add_without_offer_returns_422(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers",
        data={"offer_id": "", "idempotency_key": "ui-empty"},
    )

    assert response.status_code == 422
    assert "выберите оффер" in response.text
    assert "update_stream" not in stub.names


async def test_editor_add_with_non_positive_offer_returns_422(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """`0` и отрицательные ID — валидный `int()`, но не валидный оффер.

    До фикса такие значения доходили до `AddOfferRequest` (у него `gt=0`),
    а `ValidationError` возникал внутри `try`, где ловятся только известные
    ошибки: страница отвечала 500 (в тесте — исключением наружу). Ожидаем
    страницу редактора с понятным текстом и без обращений к Keitaro.
    """
    for value in ("0", "-5"):
        response = await ui_client.post(
            f"/ui/editor/{CAMPAIGN_ID}/offers",
            data={"offer_id": value, "idempotency_key": f"ui-bad-{value}"},
        )

        assert response.status_code == 422
        assert "больше нуля" in response.text
        # Форма вернулась страницей, а не JSON-ошибкой FastAPI.
        assert 'name="idempotency_key"' in response.text
        assert "update_stream" not in stub.names


async def test_editor_removes_offer_and_rebalances(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.streams = two_offer_stream()

    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers/{SECOND_OFFER_ID}/delete",
        data={"idempotency_key": "ui-remove", "interval": "7_days_ago"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/ui/editor?campaign_id={CAMPAIGN_ID}&action=removed&interval=7_days_ago"
    )
    page = await ui_client.get(response.headers["location"])
    assert "Оффер удалён" in page.text
    assert "100%" in page.text


async def test_editor_delete_keys_differ_for_same_offer_in_two_streams(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    """До фикса ключ удаления зависел только от оффера.

    Один и тот же оффер в двух потоках получал один ключ: первое удаление
    срабатывало, второе возвращало «Оффер удалён» из журнала, хотя во втором
    потоке оффер оставался. Теперь ключ и поток — часть действия формы.
    """
    stub.streams = shared_offer_streams()

    page = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")
    forms = delete_forms(page.text)

    assert [(form["offer_id"], form["stream_id"]) for form in forms] == [
        (str(OFFER_ID), "1"),
        (str(SECOND_OFFER_ID), "1"),
        (str(OFFER_ID), "2"),
        (str(SECOND_OFFER_ID), "2"),
    ]
    assert len({form["idempotency_key"] for form in forms}) == 4

    # Удаляем тот же оффер из второго потока: первый должен остаться нетронутым.
    target = next(
        form for form in forms if form["offer_id"] == str(OFFER_ID) and form["stream_id"] == "2"
    )
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers/{OFFER_ID}/delete",
        data={"idempotency_key": target["idempotency_key"], "stream_id": "2"},
    )

    assert response.status_code == 303
    assert "action=removed" in response.headers["location"]
    updated = [call for name, call in stub.calls if name == "update_stream"]
    assert [stream_id for stream_id, _ in updated] == [2]

    # Тот же оффер из первого потока — отдельное действие со своим ключом.
    page = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")
    again = next(form for form in delete_forms(page.text) if form["offer_id"] == str(OFFER_ID))
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers/{OFFER_ID}/delete",
        data={"idempotency_key": again["idempotency_key"], "stream_id": again["stream_id"]},
    )

    assert response.status_code == 303
    assert "action=removed" in response.headers["location"]
    # Два PUT в разные потоки: раньше второй запрос был реплеем журнала.
    updated = [call for name, call in stub.calls if name == "update_stream"]
    assert [stream_id for stream_id, _ in updated] == [2, 1]
    remaining = {stream.id: [offer.offer_id for offer in stream.offers] for stream in stub.streams}
    assert remaining[1] == [SECOND_OFFER_ID]
    assert remaining[2] == [SECOND_OFFER_ID]


async def test_editor_cannot_remove_last_offer(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    response = await ui_client.post(
        f"/ui/editor/{CAMPAIGN_ID}/offers/{OFFER_ID}/delete",
        data={"idempotency_key": "ui-last"},
    )

    assert response.status_code == 422
    assert "последний оффер" in response.text
    assert "update_stream" not in stub.names


async def test_editor_add_is_idempotent_by_hidden_key(
    ui_client: httpx.AsyncClient, stub: StubKeitaro, db_engine: AsyncEngine
) -> None:
    data = {"offer_id": str(SECOND_OFFER_ID), "idempotency_key": "ui-add-repeat"}

    first = await ui_client.post(f"/ui/editor/{CAMPAIGN_ID}/offers", data=data)
    second = await ui_client.post(f"/ui/editor/{CAMPAIGN_ID}/offers", data=data)

    assert first.status_code == 303
    assert second.status_code == 303
    assert stub.names.count("update_stream") == 1
    assert len(await journal(db_engine)) == 1


async def test_editor_refresh_forces_keitaro_calls(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")
    campaigns_before = stub.names.count("list_campaigns")
    offers_before = stub.names.count("list_offers")

    response = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}&refresh=1")

    assert response.status_code == 200
    assert stub.names.count("list_campaigns") == campaigns_before + 1
    assert stub.names.count("list_offers") == offers_before + 1


async def test_editor_escapes_campaign_name(
    ui_client: httpx.AsyncClient, stub: StubKeitaro
) -> None:
    stub.campaigns = [
        Campaign(
            id=CAMPAIGN_ID,
            name="<script>alert(1)</script>",
            alias="xss",
            state="active",
            type="position",
        )
    ]

    response = await ui_client.get(f"/ui/editor?campaign_id={CAMPAIGN_ID}")

    assert response.status_code == 200
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.text
