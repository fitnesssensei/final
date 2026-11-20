"""DTO Admin API Keitaro.

Модели описывают только те поля, которые нужны приложению: трекер отдаёт
десятки служебных полей, и жёсткая схема сломалась бы при их изменении.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# Значения обязательного query-параметра `type` для `GET /groups`
# (на живом инстансе без него приходит 400).
GroupType = Literal["campaigns", "offers", "domains", "landings", "flows"]

StreamSchema = Literal["landings", "redirect", "action"]


class KeitaroModel(BaseModel):
    """Базовая модель ответа: лишние поля игнорируются."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class Campaign(KeitaroModel):
    """Кампания из `GET /campaigns[/{id}]`."""

    id: int
    name: str
    alias: str
    state: str | None = None
    type: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None
    token: str | None = None
    created_at: str | None = None


class StreamFilter(KeitaroModel):
    """Фильтр потока: у гео-потока это `country` в режиме `accept`.

    `payload` приходит в двух формах: список (`["MX"]`) и строка с пробелами
    (`"RU AU"` — проверено на живом инстансе). Обе формы надо сохранять как
    есть, иначе редактор не прочитает поток и не вернёт его в `PUT`.
    """

    name: str
    mode: str | None = None
    payload: list[str] | str = Field(default_factory=list)


class StreamOffer(KeitaroModel):
    """Оффер внутри потока с долей трафика `share` (в процентах)."""

    offer_id: int
    share: int
    state: str | None = None


class Stream(KeitaroModel):
    """Поток кампании из `GET /campaigns/{id}/streams`.

    `triggers`/`landings`/`action_options` хранятся как есть: редактор
    возвращает поток в `PUT` целиком, и потеря этих полей сбросила бы
    настройки, которые он не меняет.
    """

    id: int
    name: str
    type: str
    campaign_id: int | None = None
    # `schema` — имя поля в API; в модели занято методом BaseModel, поэтому alias
    schema_: str | None = Field(default=None, alias="schema")
    action_type: str | None = None
    action_payload: str | None = None
    # Живой Keitaro отдаёт здесь null, объект или список — сохраняем как есть
    action_options: dict[str, Any] | list[Any] | None = None
    position: int | None = None
    weight: int | None = None
    state: str | None = None
    comments: str | None = None
    filter_or: bool | None = None
    offer_selection: str | None = None
    collect_clicks: bool | None = None
    filters: list[StreamFilter] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    landings: list[dict[str, Any]] = Field(default_factory=list)
    offers: list[StreamOffer] = Field(default_factory=list)


class Offer(KeitaroModel):
    """Оффер из `GET /offers[/{id}]`; `country` — список ISO-2."""

    id: int
    name: str
    country: list[str] = Field(default_factory=list)
    group_id: int | None = None
    action_type: str | None = None
    action_payload: str | None = None
    state: str | None = None


class Domain(KeitaroModel):
    """Домен кампании из `GET /domains`."""

    id: int
    name: str
    state: str | None = None
    is_ssl: bool | None = None


class Group(KeitaroModel):
    """Группа кампаний/офферов из `GET /groups?type=...`."""

    id: int
    name: str
    type: str | None = None
    position: int | None = None


class TrafficSource(KeitaroModel):
    """Источник трафика из `GET /traffic_sources`."""

    id: int
    name: str
    state: str | None = None


class ReportRow(KeitaroModel):
    """Строка отчёта: набор измерений зависит от `dimensions` в запросе.

    Поэтому `extra="allow"`: при запросе `["campaign", "offer"]` в строке
    не будет `stream`, зато появятся другие измерения.
    """

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    campaign: str | None = None
    campaign_id: int | None = None
    stream: str | None = None
    offer: str | None = None
    clicks: int = 0
    conversions: int = 0


class ReportResponse(KeitaroModel):
    """Ответ `POST /report/build`."""

    rows: list[ReportRow] = Field(default_factory=list)
    total: int | None = None


class CampaignCreate(BaseModel):
    """Тело `POST /campaigns`: alias и name обязательны."""

    model_config = ConfigDict(extra="forbid")

    alias: str
    name: str
    type: str = "position"
    state: str = "active"
    domain_id: int
    group_id: int
    traffic_source_id: int


class StreamCreate(BaseModel):
    """Тело `POST /streams` (и базовые поля для `PUT /streams/{id}`)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    campaign_id: int
    name: str
    type: str
    schema_: StreamSchema = Field(alias="schema")
    action_type: str = "http"
    action_payload: str = ""
    collect_clicks: bool = True
    position: int
    filters: list[StreamFilter] = Field(default_factory=list)
    offers: list[StreamOffer] = Field(default_factory=list)


class StreamUpdate(BaseModel):
    """Тело `PUT /streams/{id}`: полный поток, который возвращает `GET`.

    Редактор меняет только `offers`, но отправляет и остальные поля: Keitaro
    при обновлении потока ждёт объект целиком. `None` не отправляется
    (`exclude_none`), поэтому незаполненные поля не перетираются дефолтами.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    campaign_id: int
    name: str
    type: str
    schema_: StreamSchema = Field(alias="schema")
    action_type: str | None = None
    action_payload: str | None = None
    action_options: dict[str, Any] | list[Any] | None = None
    collect_clicks: bool | None = None
    position: int
    weight: int | None = None
    state: str | None = None
    comments: str | None = None
    filter_or: bool | None = None
    offer_selection: str | None = None
    filters: list[StreamFilter] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    landings: list[dict[str, Any]] = Field(default_factory=list)
    offers: list[StreamOffer] = Field(default_factory=list)


class ReportRequest(BaseModel):
    """Тело `POST /report/build`.

    Рабочие значения для инстанса: `dimensions=["campaign","stream","offer"]`,
    `measures=["clicks","conversions"]`, `range={"interval": "all_time"}`.
    """

    model_config = ConfigDict(extra="forbid")

    dimensions: list[str]
    measures: list[str]
    range: dict[str, str] = Field(default_factory=lambda: {"interval": "all_time"})
