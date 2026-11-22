"""Тесты клиента Keitaro на моках respx: реальная сеть не используется."""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from keitaro_wrapper.keitaro import (
    CampaignCreate,
    KeitaroAuthError,
    KeitaroClient,
    KeitaroError,
    KeitaroNotFound,
    KeitaroUnavailable,
    KeitaroValidationError,
    ReportRequest,
    StreamCreate,
    StreamUpdate,
)

BASE = "https://example.keitaro.io"
API = f"{BASE}/admin_api/v1"

CAMPAIGN = {
    "id": 42,
    "name": "MX test",
    "alias": "mx-test-ab12cd",
    "state": "active",
    "type": "position",
    "domain_id": 1813,
    "group_id": 10332,
    "traffic_source_id": 836,
    # Лишнее поле: реальный Keitaro отдаёт десятки служебных полей
    "junk_field": "игнорируется",
}

STREAM = {
    "id": 7,
    "name": "Offers",
    "type": "default",
    "campaign_id": 42,
    "schema": "landings",
    "action_type": "http",
    "action_payload": "",
    "position": 2,
    "collect_clicks": True,
    "filters": [],
    "offers": [{"offer_id": 18813, "share": 100, "state": "active"}],
}


@pytest.fixture
def sleeps() -> list[float]:
    """Собранные паузы ретраев, чтобы тесты не ждали по-настоящему."""
    return []


@pytest.fixture
async def keitaro(sleeps: list[float]) -> AsyncIterator[KeitaroClient]:
    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    client = KeitaroClient(API, "test-key", retries=2, backoff=0.5, sleep=fake_sleep)
    async with client:
        yield client


@respx.mock
async def test_get_campaign_sends_api_key_and_parses(keitaro: KeitaroClient) -> None:
    route = respx.get(f"{API}/campaigns/42").mock(return_value=httpx.Response(200, json=CAMPAIGN))

    campaign = await keitaro.get_campaign(42)

    assert campaign.id == 42
    assert campaign.alias == "mx-test-ab12cd"
    request = route.calls.last.request
    assert request.headers["Api-Key"] == "test-key"
    assert str(request.url) == f"{API}/campaigns/42"


@respx.mock
async def test_api_key_is_stripped_before_sending() -> None:
    """Клиент — самостоятельный слой: ключ из скрипта с пробелами не должен давать 401."""
    route = respx.get(f"{API}/campaigns").mock(return_value=httpx.Response(200, json=[]))
    client = KeitaroClient(API, "  key-with-spaces  ")
    async with client:
        await client.list_campaigns()

    assert route.calls.last.request.headers["Api-Key"] == "key-with-spaces"


@respx.mock
async def test_list_campaigns_parses_each_item(keitaro: KeitaroClient) -> None:
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(200, json=[CAMPAIGN, {**CAMPAIGN, "id": 43}])
    )

    campaigns = await keitaro.list_campaigns()

    assert [item.id for item in campaigns] == [42, 43]


@respx.mock
async def test_create_campaign_posts_full_payload(keitaro: KeitaroClient) -> None:
    route = respx.post(f"{API}/campaigns").mock(return_value=httpx.Response(200, json=CAMPAIGN))
    payload = CampaignCreate(
        alias="mx-test-ab12cd",
        name="MX test",
        domain_id=1813,
        group_id=10332,
        traffic_source_id=836,
    )

    campaign = await keitaro.create_campaign(payload)

    assert campaign.id == 42
    assert json.loads(route.calls.last.request.content) == {
        "alias": "mx-test-ab12cd",
        "name": "MX test",
        "type": "position",
        "state": "active",
        "domain_id": 1813,
        "group_id": 10332,
        "traffic_source_id": 836,
    }


@respx.mock
async def test_delete_campaign_accepts_empty_body(keitaro: KeitaroClient) -> None:
    respx.delete(f"{API}/campaigns/42").mock(return_value=httpx.Response(200))

    assert await keitaro.delete_campaign(42) is None


@respx.mock
async def test_list_campaign_streams_parses_schema_and_offers(keitaro: KeitaroClient) -> None:
    respx.get(f"{API}/campaigns/42/streams").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 6,
                    "name": "Google (MX)",
                    "type": "regular",
                    "campaign_id": 42,
                    "schema": "redirect",
                    "action_type": "http",
                    "action_payload": "https://www.google.com",
                    "position": 1,
                    "filters": [{"name": "country", "mode": "accept", "payload": ["MX"], "oid": 1}],
                    "offers": [],
                },
                STREAM,
            ],
        )
    )

    streams = await keitaro.list_campaign_streams(42)

    assert streams[0].schema_ == "redirect"
    assert streams[0].filters[0].payload == ["MX"]
    assert streams[1].offers[0].offer_id == 18813
    assert streams[1].offers[0].share == 100


@respx.mock
async def test_stream_filter_string_payload_is_preserved(keitaro: KeitaroClient) -> None:
    """Живой Keitaro отдаёт `country` и строкой `"RU AU"` — это не ошибка."""
    respx.get(f"{API}/campaigns/42/streams").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 6,
                    "name": "Google (RU)",
                    "type": "regular",
                    "campaign_id": 42,
                    "schema": "redirect",
                    "action_type": "http",
                    "position": 1,
                    "filters": [{"name": "country", "mode": "accept", "payload": "RU AU"}],
                    "offers": [],
                }
            ],
        )
    )

    streams = await keitaro.list_campaign_streams(42)

    assert streams[0].filters[0].payload == "RU AU"


@respx.mock
async def test_stream_action_options_accepts_null_and_list(keitaro: KeitaroClient) -> None:
    """Живой Keitaro отдаёт `action_options` как null, объект или список."""
    respx.get(f"{API}/campaigns/42/streams").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": 6,
                    "name": "A",
                    "type": "regular",
                    "schema": "redirect",
                    "action_type": "http",
                    "position": 1,
                    "action_options": None,
                },
                {
                    "id": 7,
                    "name": "B",
                    "type": "default",
                    "schema": "landings",
                    "action_type": "http",
                    "position": 2,
                    "action_options": [{"name": "option"}],
                },
            ],
        )
    )

    streams = await keitaro.list_campaign_streams(42)

    assert streams[0].action_options is None
    assert streams[1].action_options == [{"name": "option"}]


@respx.mock
async def test_create_stream_sends_schema_alias_and_offers(keitaro: KeitaroClient) -> None:
    route = respx.post(f"{API}/streams").mock(return_value=httpx.Response(200, json=STREAM))
    payload = StreamCreate(
        campaign_id=42,
        name="Offers",
        type="default",
        schema_="landings",
        position=2,
        offers=[{"offer_id": 18813, "share": 100}],
    )

    await keitaro.create_stream(payload)

    sent = json.loads(route.calls.last.request.content)
    assert sent["schema"] == "landings"
    assert "schema_" not in sent
    # state=None у оффера не должен уходить в API
    assert sent["offers"] == [{"offer_id": 18813, "share": 100}]


@respx.mock
async def test_update_stream_puts_payload(keitaro: KeitaroClient) -> None:
    """Правка потока идёт через PUT — без этого редактор не сможет менять офферы."""
    route = respx.put(f"{API}/streams/7").mock(return_value=httpx.Response(200, json=STREAM))
    payload = StreamCreate(
        campaign_id=42,
        name="Offers",
        type="default",
        schema_="landings",
        position=2,
        offers=[{"offer_id": 18813, "share": 100}],
    )

    stream = await keitaro.update_stream(7, payload)

    assert stream.id == 7
    sent = json.loads(route.calls.last.request.content)
    assert sent["schema"] == "landings"
    assert sent["campaign_id"] == 42


@respx.mock
async def test_update_stream_sends_full_stream(keitaro: KeitaroClient) -> None:
    """Редактор шлёт полный поток: triggers/landings/вес не должны потеряться."""
    route = respx.put(f"{API}/streams/7").mock(return_value=httpx.Response(200, json=STREAM))
    payload = StreamUpdate(
        campaign_id=42,
        name="Offers",
        type="default",
        schema_="landings",
        action_type="http",
        action_payload="",
        collect_clicks=True,
        position=2,
        weight=100,
        state="active",
        comments="поток офферов",
        filter_or=True,
        offer_selection="before_click",
        filters=[{"name": "country", "mode": "accept", "payload": ["MX"]}],
        triggers=[{"name": "conversion"}],
        landings=[{"landing_id": 5, "share": 100}],
        offers=[{"offer_id": 18813, "share": 100}],
    )

    await keitaro.update_stream(7, payload)

    assert json.loads(route.calls.last.request.content) == {
        "campaign_id": 42,
        "name": "Offers",
        "type": "default",
        "schema": "landings",
        "action_type": "http",
        "action_payload": "",
        "collect_clicks": True,
        "position": 2,
        "weight": 100,
        "state": "active",
        "comments": "поток офферов",
        "filter_or": True,
        "offer_selection": "before_click",
        "filters": [{"name": "country", "mode": "accept", "payload": ["MX"]}],
        "triggers": [{"name": "conversion"}],
        "landings": [{"landing_id": 5, "share": 100}],
        "offers": [{"offer_id": 18813, "share": 100}],
    }


@respx.mock
async def test_list_groups_sends_required_type(keitaro: KeitaroClient) -> None:
    route = respx.get(f"{API}/groups", params={"type": "offers"}).mock(
        return_value=httpx.Response(
            200, json=[{"id": 1, "name": "Offers", "type": "offers", "position": 1}]
        )
    )

    groups = await keitaro.list_groups("offers")

    assert groups[0].type == "offers"
    assert route.calls.last.request.url.params["type"] == "offers"


@respx.mock
async def test_build_report_posts_body_and_parses_rows(keitaro: KeitaroClient) -> None:
    route = respx.post(f"{API}/report/build").mock(
        return_value=httpx.Response(
            200,
            json={
                "rows": [
                    {
                        "campaign": "MX test",
                        "campaign_id": 42,
                        "stream": "Google (MX)",
                        "offer": None,
                        "clicks": 10,
                        "conversions": 0,
                    }
                ],
                "total": 1,
            },
        )
    )
    payload = ReportRequest(
        dimensions=["campaign", "stream", "offer"], measures=["clicks", "conversions"]
    )

    report = await keitaro.build_report(payload)

    assert report.total == 1
    assert report.rows[0].campaign_id == 42
    assert report.rows[0].clicks == 10
    assert json.loads(route.calls.last.request.content) == {
        "dimensions": ["campaign", "stream", "offer"],
        "measures": ["clicks", "conversions"],
        "range": {"interval": "all_time"},
    }


@respx.mock
async def test_build_report_sends_filters_and_skips_none(keitaro: KeitaroClient) -> None:
    """Фильтр по кампании уходит в API, `filters=None` — не уходит вовсе."""
    route = respx.post(f"{API}/report/build").mock(
        return_value=httpx.Response(200, json={"rows": [], "total": 0})
    )
    payload = ReportRequest(
        dimensions=["campaign_id", "offer"],
        measures=["clicks", "conversions"],
        filters=[{"name": "campaign_id", "operator": "EQUALS", "expression": 42}],
    )

    await keitaro.build_report(payload)

    sent = json.loads(route.calls.last.request.content)
    assert sent["filters"] == [{"name": "campaign_id", "operator": "EQUALS", "expression": 42}]

    await keitaro.build_report(
        ReportRequest(dimensions=["campaign_id", "offer"], measures=["clicks", "conversions"])
    )

    assert "filters" not in json.loads(route.calls.last.request.content)


@respx.mock
async def test_401_maps_to_auth_error(keitaro: KeitaroClient) -> None:
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(401, json={"error": "Invalid API key"})
    )

    with pytest.raises(KeitaroAuthError) as exc_info:
        await keitaro.list_campaigns()

    assert exc_info.value.status == 401
    assert "Invalid API key" in str(exc_info.value)


@respx.mock
async def test_403_maps_to_auth_error_with_keitaro_text(keitaro: KeitaroClient) -> None:
    respx.get(f"{API}/offers").mock(
        return_value=httpx.Response(
            403, json={"error": "You have no permission to access this page"}
        )
    )

    with pytest.raises(KeitaroAuthError) as exc_info:
        await keitaro.list_offers()

    assert exc_info.value.status == 403
    assert "no permission" in str(exc_info.value)


@respx.mock
async def test_404_maps_to_not_found(keitaro: KeitaroClient) -> None:
    respx.get(f"{API}/offers/999").mock(
        return_value=httpx.Response(404, json={"error": "Offer not found"})
    )

    with pytest.raises(KeitaroNotFound) as exc_info:
        await keitaro.get_offer(999)

    assert exc_info.value.status == 404
    assert exc_info.value.path == "/offers/999"


@respx.mock
async def test_422_field_errors_are_flattened(keitaro: KeitaroClient) -> None:
    """Живой Keitaro отдаёт 422 ошибками по полям, а не `{"error": ...}`."""
    respx.post(f"{API}/campaigns").mock(
        return_value=httpx.Response(
            422, json={"name": ["Name is required"], "alias": ["Alias is required"]}
        )
    )
    payload = CampaignCreate(
        alias="", name="", domain_id=1813, group_id=10332, traffic_source_id=836
    )

    with pytest.raises(KeitaroValidationError) as exc_info:
        await keitaro.create_campaign(payload)

    message = str(exc_info.value)
    assert "name: Name is required" in message
    assert "alias: Alias is required" in message
    assert exc_info.value.payload == {
        "name": ["Name is required"],
        "alias": ["Alias is required"],
    }


@respx.mock
async def test_plain_text_error_is_preserved(keitaro: KeitaroClient) -> None:
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(400, text="Param 'type' is required")
    )

    with pytest.raises(KeitaroValidationError) as exc_info:
        await keitaro.list_campaigns()

    assert "Param 'type' is required" in str(exc_info.value)


@respx.mock
async def test_non_json_success_body_raises_error(keitaro: KeitaroClient) -> None:
    """200 с HTML — заглушка Cloudflare или неверный KEITARO_BASE_URL, а не «данных нет»."""
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(200, text="<html><body>maintenance</body></html>")
    )

    with pytest.raises(KeitaroError) as exc_info:
        await keitaro.list_campaigns()

    message = str(exc_info.value)
    assert "ответ не JSON" in message
    assert "maintenance" in message
    assert exc_info.value.path == "/campaigns"


@respx.mock
async def test_non_json_error_body_still_maps_by_status(
    keitaro: KeitaroClient, sleeps: list[float]
) -> None:
    """HTML на 502 — по-прежнему KeitaroUnavailable: для ошибок разбор JSON не обязателен."""
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(502, text="<html>Bad gateway</html>")
    )

    with pytest.raises(KeitaroUnavailable) as exc_info:
        await keitaro.list_campaigns()

    assert exc_info.value.status == 502
    assert "Bad gateway" in str(exc_info.value)
    assert sleeps == [0.5, 1.0]


@respx.mock
async def test_dict_instead_of_list_raises_domain_error(keitaro: KeitaroClient) -> None:
    """Объект вместо списка — доменная ошибка с путём, а не pydantic ValidationError."""
    respx.get(f"{API}/campaigns").mock(return_value=httpx.Response(200, json={"id": 42}))

    with pytest.raises(KeitaroError) as exc_info:
        await keitaro.list_campaigns()

    assert "список" in str(exc_info.value)
    assert exc_info.value.path == "/campaigns"


@respx.mock
async def test_list_item_with_wrong_shape_raises_domain_error(keitaro: KeitaroClient) -> None:
    """Ошибка валидации элемента должна показывать, какое поле не пришло."""
    respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(200, json=[{"name": "без id и alias"}])
    )

    with pytest.raises(KeitaroError) as exc_info:
        await keitaro.list_campaigns()

    message = str(exc_info.value)
    assert "неверный формат" in message
    assert "id" in message


@respx.mock
async def test_list_instead_of_object_raises_domain_error(keitaro: KeitaroClient) -> None:
    """Для одиночной сущности список — тоже неверный формат."""
    respx.get(f"{API}/offers/999").mock(return_value=httpx.Response(200, json=[]))

    with pytest.raises(KeitaroError) as exc_info:
        await keitaro.get_offer(999)

    assert "объект" in str(exc_info.value)
    assert exc_info.value.path == "/offers/999"


@respx.mock
async def test_500_is_retried_then_succeeds(keitaro: KeitaroClient, sleeps: list[float]) -> None:
    route = respx.get(f"{API}/campaigns").mock(
        side_effect=[
            httpx.Response(500, json={"error": "boom"}),
            httpx.Response(200, json=[CAMPAIGN]),
        ]
    )

    campaigns = await keitaro.list_campaigns()

    assert route.call_count == 2
    assert sleeps == [0.5]
    assert campaigns[0].id == 42


@respx.mock
async def test_retries_exhausted_raises_unavailable(
    keitaro: KeitaroClient, sleeps: list[float]
) -> None:
    route = respx.get(f"{API}/campaigns").mock(
        return_value=httpx.Response(503, json={"error": "overloaded"})
    )

    with pytest.raises(KeitaroUnavailable) as exc_info:
        await keitaro.list_campaigns()

    # Одна попытка и два ретрая, паузы растут экспоненциально
    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]
    assert exc_info.value.status == 503
    assert "overloaded" in str(exc_info.value)


@respx.mock
async def test_retry_after_header_is_honored(keitaro: KeitaroClient, sleeps: list[float]) -> None:
    respx.get(f"{API}/campaigns").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "3"}, json={"error": "slow down"}),
            httpx.Response(200, json=[]),
        ]
    )

    assert await keitaro.list_campaigns() == []
    assert sleeps == [3.0]


@respx.mock
async def test_timeout_is_retried_and_mapped_to_unavailable(
    keitaro: KeitaroClient, sleeps: list[float]
) -> None:
    route = respx.get(f"{API}/campaigns").mock(side_effect=httpx.ReadTimeout("timed out"))

    with pytest.raises(KeitaroUnavailable) as exc_info:
        await keitaro.list_campaigns()

    assert route.call_count == 3
    assert len(sleeps) == 2
    assert "сеть недоступна" in str(exc_info.value)
    # У ReadTimeout пустой текст: в сообщении должно остаться имя класса
    assert "ReadTimeout" in str(exc_info.value)


@respx.mock
async def test_post_5xx_is_not_retried(keitaro: KeitaroClient, sleeps: list[float]) -> None:
    """Повтор POST после 5xx мог бы создать дубль кампании — не повторяем."""
    route = respx.post(f"{API}/campaigns").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    payload = CampaignCreate(
        alias="mx-test-ab12cd",
        name="MX test",
        domain_id=1813,
        group_id=10332,
        traffic_source_id=836,
    )

    with pytest.raises(KeitaroUnavailable):
        await keitaro.create_campaign(payload)

    assert route.call_count == 1
    assert sleeps == []


@respx.mock
async def test_post_read_timeout_is_not_retried(
    keitaro: KeitaroClient, sleeps: list[float]
) -> None:
    """ReadTimeout приходит после отправки: запрос мог примениться, повтор дал бы дубль."""
    route = respx.post(f"{API}/campaigns").mock(side_effect=httpx.ReadTimeout("slow"))
    payload = CampaignCreate(
        alias="mx-test-ab12cd",
        name="MX test",
        domain_id=1813,
        group_id=10332,
        traffic_source_id=836,
    )

    with pytest.raises(KeitaroUnavailable) as exc_info:
        await keitaro.create_campaign(payload)

    assert route.call_count == 1
    assert sleeps == []
    assert "ReadTimeout" in str(exc_info.value)


@respx.mock
async def test_post_connect_error_is_retried(keitaro: KeitaroClient, sleeps: list[float]) -> None:
    """Соединение не установилось — запрос точно не ушёл, повтор безопасен."""
    route = respx.post(f"{API}/campaigns").mock(
        side_effect=[httpx.ConnectError("refused"), httpx.Response(200, json=CAMPAIGN)]
    )
    payload = CampaignCreate(
        alias="mx-test-ab12cd",
        name="MX test",
        domain_id=1813,
        group_id=10332,
        traffic_source_id=836,
    )

    campaign = await keitaro.create_campaign(payload)

    assert campaign.id == 42
    assert route.call_count == 2
    assert sleeps == [0.5]


@respx.mock
async def test_post_429_is_retried(keitaro: KeitaroClient, sleeps: list[float]) -> None:
    """429 значит «запрос отклонён», поэтому повтор безопасен и для POST."""
    route = respx.post(f"{API}/campaigns").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}, json={"error": "slow down"}),
            httpx.Response(200, json=CAMPAIGN),
        ]
    )
    payload = CampaignCreate(
        alias="mx-test-ab12cd",
        name="MX test",
        domain_id=1813,
        group_id=10332,
        traffic_source_id=836,
    )

    campaign = await keitaro.create_campaign(payload)

    assert campaign.id == 42
    assert route.call_count == 2
    assert sleeps == [1.0]


def test_client_requires_credentials() -> None:
    with pytest.raises(ValueError, match="KEITARO_"):
        KeitaroClient("", "test-key")
    with pytest.raises(ValueError, match="KEITARO_"):
        KeitaroClient(API, "   ")


async def test_repr_hides_api_key() -> None:
    async with KeitaroClient(API, "super-secret-key") as client:
        assert "super-secret-key" not in repr(client)
        assert not client.is_closed
    assert client.is_closed
