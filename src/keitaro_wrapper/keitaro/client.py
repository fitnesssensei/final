"""Асинхронный клиент Keitaro Admin API v1.

Клиент не читает настройки сам: адрес и ключ передаёт вызывающий код
(`lifespan` приложения). В тестах транспорт подменяется через respx,
поэтому ни один тест не ходит в сеть.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from keitaro_wrapper.keitaro.dto import (
    Campaign,
    CampaignCreate,
    Domain,
    Group,
    GroupType,
    Offer,
    ReportRequest,
    ReportResponse,
    Stream,
    StreamCreate,
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

logger = logging.getLogger(__name__)

# 429 и 5xx имеет смысл повторять: трекер перегружен или временно недоступен.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
# Повторять 5xx можно только для методов, которые не создают сущности:
# POST мог уже примениться на стороне Keitaro, и повтор дал бы дубль.
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "PUT", "DELETE"})
# Сетевые сбои, которые лечатся повтором: таймаут, обрыв, отказ соединения.
RETRY_EXCEPTIONS = (httpx.TimeoutException, httpx.TransportError)

Sleep = Callable[[float], Awaitable[None]]


def _format_validation_error(exc: ValidationError) -> str:
    """Первые ошибки pydantic одной строкой: `id: Field required; alias: ...`."""
    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


def _parse_list[ModelT: BaseModel](
    model: type[ModelT], data: Any, *, method: str, path: str
) -> list[ModelT]:
    """Разбирает список сущностей, превращая неверный формат в доменную ошибку.

    Пустое тело (204/200 без контента) — это «данных нет», а объект вместо
    списка — ошибка контракта: выше по стеку её надо показывать как ошибку
    Keitaro, а не как 500 от pydantic.
    """
    if data is None:
        return []
    if not isinstance(data, list):
        raise KeitaroError(
            f"{method} {path} -> ожидался список, пришло {type(data).__name__}",
            method=method,
            path=path,
        )
    try:
        return [model.model_validate(item) for item in data]
    except ValidationError as exc:
        raise KeitaroError(
            f"{method} {path} -> неверный формат элемента: {_format_validation_error(exc)}",
            method=method,
            path=path,
        ) from exc


def _parse_model[ModelT: BaseModel](
    model: type[ModelT], data: Any, *, method: str, path: str
) -> ModelT:
    """Разбирает одну сущность; вместо `ValidationError` поднимает `KeitaroError`."""
    if data is None:
        raise KeitaroError(
            f"{method} {path} -> пустой ответ вместо объекта", method=method, path=path
        )
    if not isinstance(data, dict):
        raise KeitaroError(
            f"{method} {path} -> ожидался объект, пришло {type(data).__name__}",
            method=method,
            path=path,
        )
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise KeitaroError(
            f"{method} {path} -> неверный формат ответа: {_format_validation_error(exc)}",
            method=method,
            path=path,
        ) from exc


class KeitaroClient:
    """Обёртка над httpx: заголовок `Api-Key`, ретраи и доменные ошибки."""

    def __init__(
        self,
        api_root: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        retries: int = 2,
        backoff: float = 0.5,
        max_backoff: float = 8.0,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if not api_root.strip() or not api_key.strip():
            raise ValueError("нужны адрес Admin API и ключ: проверьте KEITARO_* в .env")
        self.api_root = api_root.rstrip("/")
        # Ключ обрезаем здесь, а не только в Settings: клиент конструируют
        # и скрипты, а `Api-Key` с пробелами Keitaro отвергает как 401.
        self._api_key = api_key.strip()
        self._retries = retries
        self._backoff = backoff
        self._max_backoff = max_backoff
        # `sleep` внедряется ради тестов: ретраи проверяются без реальных пауз
        self._sleep = sleep
        self._http = httpx.AsyncClient(
            base_url=self.api_root,
            headers={"Api-Key": self._api_key, "Accept": "application/json"},
            timeout=timeout,
        )

    def __repr__(self) -> str:
        """Ключ не должен попадать в логи даже через repr клиента."""
        return f"KeitaroClient(api_root={self.api_root!r}, api_key='***')"

    @property
    def is_closed(self) -> bool:
        """Закрыт ли базовый httpx-клиент (проверяется в тестах жизненного цикла)."""
        return self._http.is_closed

    async def aclose(self) -> None:
        """Закрывает соединения; вызывается в `finally` у `lifespan`."""
        await self._http.aclose()

    async def __aenter__(self) -> KeitaroClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    @staticmethod
    def _retry_delay(attempt: int, retry_after: str | None, backoff: float, cap: float) -> float:
        """Пауза перед повтором: `Retry-After` трекера либо экспоненциальный backoff."""
        if retry_after:
            try:
                return min(float(retry_after), cap)
            except ValueError:
                # Некорректный заголовок игнорируем: важнее не ждать лишнего
                pass
        return min(backoff * (2**attempt), cap)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        """Выполняет запрос с ретраями и разбором ответа в доменные ошибки.

        GET/PUT/DELETE повторяются на 429 и 5xx и на любых сетевых сбоях.
        POST — только на 429 и на ошибках соединения (`ConnectError`,
        `ConnectTimeout`): в остальных случаях запрос мог уже примениться,
        и повтор создал бы дубль.
        """
        attempt = 0
        while True:
            try:
                response = await self._http.request(method, path, params=params, json=body)
            except RETRY_EXCEPTIONS as exc:
                # У некоторых сетевых ошибок пустой str(exc) — без имени класса
                # в логе осталось бы бесполезное «сеть ()»
                reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
                # POST повторяем, только если запрос точно не ушёл: соединение
                # не установилось. После ReadTimeout/ReadError Keitaro мог уже
                # создать сущность, и повтор дал бы дубль.
                can_retry = method in IDEMPOTENT_METHODS or isinstance(
                    exc, (httpx.ConnectError, httpx.ConnectTimeout)
                )
                if attempt >= self._retries or not can_retry:
                    raise KeitaroUnavailable(
                        f"{method} {path} -> сеть недоступна: {reason}", method=method, path=path
                    ) from exc
                delay = self._retry_delay(attempt, None, self._backoff, self._max_backoff)
                logger.warning(
                    "Keitaro %s %s: сеть (%s), повтор %s/%s через %.1f c",
                    method,
                    path,
                    reason,
                    attempt + 1,
                    self._retries,
                    delay,
                )
                await self._sleep(delay)
                attempt += 1
                continue

            if response.status_code in RETRY_STATUSES and attempt < self._retries:
                # POST повторяем только на 429: там трекер явно отказал и ничего не создал
                can_retry = method in IDEMPOTENT_METHODS or response.status_code == 429
                if can_retry:
                    delay = self._retry_delay(
                        attempt,
                        response.headers.get("Retry-After"),
                        self._backoff,
                        self._max_backoff,
                    )
                    logger.warning(
                        "Keitaro %s %s: HTTP %s, повтор %s/%s через %.1f c",
                        method,
                        path,
                        response.status_code,
                        attempt + 1,
                        self._retries,
                        delay,
                    )
                    await self._sleep(delay)
                    attempt += 1
                    continue

            return self._handle_response(method, path, response)

    @staticmethod
    def _json_or_none(response: httpx.Response) -> Any:
        """JSON тела ошибки, если он есть: разбор не обязателен, текст берём из body."""
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _success_payload(method: str, path: str, response: httpx.Response) -> Any:
        """JSON успешного ответа; пустое тело — None, не-JSON — доменная ошибка.

        «200 + HTML» отдают заглушка Cloudflare, страница обслуживания и неверный
        `KEITARO_BASE_URL`. Вернуть в этом случае None значит показать вызывающему
        коду «данных нет» вместо реальной причины, поэтому такой ответ — ошибка.
        """
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            snippet = " ".join(response.text.split())[:100] or "<пустое тело>"
            raise KeitaroError(
                f"{method} {path} -> ответ не JSON: {snippet}", method=method, path=path
            ) from exc

    @staticmethod
    def _error_text(response: httpx.Response, payload: Any) -> str:
        """Человекочитаемый текст ошибки Keitaro.

        Трекер отвечает либо `{"error": "..."}`, либо ошибками по полям
        (`{"name": ["Name is required"]}`), либо HTML/текстом.
        """
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, str) and error:
                return error
            field_errors = {
                name: value for name, value in payload.items() if isinstance(value, list)
            }
            if field_errors:
                return "; ".join(
                    f"{name}: {', '.join(str(item) for item in items)}"
                    for name, items in field_errors.items()
                )
            return json.dumps(payload, ensure_ascii=False)[:300]
        text = response.text.strip()
        return text[:300] or "<пустой ответ>"

    def _handle_response(self, method: str, path: str, response: httpx.Response) -> Any:
        """Возвращает JSON успешного ответа или поднимает доменную ошибку."""
        if response.is_success:
            return self._success_payload(method, path, response)

        payload = self._json_or_none(response)
        message = f"{method} {path} -> HTTP {response.status_code}: "
        message += self._error_text(response, payload)
        details = {
            "status": response.status_code,
            "method": method,
            "path": path,
            "payload": payload,
        }
        if response.status_code in (401, 403):
            raise KeitaroAuthError(message, **details)
        if response.status_code == 404:
            raise KeitaroNotFound(message, **details)
        if response.status_code in (400, 422):
            raise KeitaroValidationError(message, **details)
        if response.status_code in RETRY_STATUSES:
            raise KeitaroUnavailable(message, **details)
        raise KeitaroError(message, **details)

    # --- Кампании и потоки -------------------------------------------------

    async def list_campaigns(self) -> list[Campaign]:
        path = "/campaigns"
        data = await self._request("GET", path)
        return _parse_list(Campaign, data, method="GET", path=path)

    async def get_campaign(self, campaign_id: int) -> Campaign:
        path = f"/campaigns/{campaign_id}"
        data = await self._request("GET", path)
        return _parse_model(Campaign, data, method="GET", path=path)

    async def create_campaign(self, payload: CampaignCreate) -> Campaign:
        path = "/campaigns"
        data = await self._request("POST", path, body=payload.model_dump(exclude_none=True))
        return _parse_model(Campaign, data, method="POST", path=path)

    async def delete_campaign(self, campaign_id: int) -> None:
        """Архивирует кампанию: Keitaro не удаляет её физически."""
        await self._request("DELETE", f"/campaigns/{campaign_id}")

    async def list_campaign_streams(self, campaign_id: int) -> list[Stream]:
        path = f"/campaigns/{campaign_id}/streams"
        data = await self._request("GET", path)
        return _parse_list(Stream, data, method="GET", path=path)

    async def create_stream(self, payload: StreamCreate) -> Stream:
        body = payload.model_dump(by_alias=True, exclude_none=True)
        path = "/streams"
        data = await self._request("POST", path, body=body)
        return _parse_model(Stream, data, method="POST", path=path)

    async def update_stream(self, stream_id: int, payload: StreamCreate | StreamUpdate) -> Stream:
        """`StreamCreate` — совместимость с создавателем; редактор шлёт полный поток."""
        body = payload.model_dump(by_alias=True, exclude_none=True)
        path = f"/streams/{stream_id}"
        data = await self._request("PUT", path, body=body)
        return _parse_model(Stream, data, method="PUT", path=path)

    # --- Справочники -------------------------------------------------------

    async def list_offers(self) -> list[Offer]:
        path = "/offers"
        data = await self._request("GET", path)
        return _parse_list(Offer, data, method="GET", path=path)

    async def get_offer(self, offer_id: int) -> Offer:
        path = f"/offers/{offer_id}"
        data = await self._request("GET", path)
        return _parse_model(Offer, data, method="GET", path=path)

    async def list_domains(self) -> list[Domain]:
        path = "/domains"
        data = await self._request("GET", path)
        return _parse_list(Domain, data, method="GET", path=path)

    async def list_groups(self, group_type: GroupType = "campaigns") -> list[Group]:
        """`type` обязателен: без него живой инстанс отвечает 400."""
        path = "/groups"
        data = await self._request("GET", path, params={"type": group_type})
        return _parse_list(Group, data, method="GET", path=path)

    async def list_traffic_sources(self) -> list[TrafficSource]:
        path = "/traffic_sources"
        data = await self._request("GET", path)
        return _parse_list(TrafficSource, data, method="GET", path=path)

    # --- Отчёты ------------------------------------------------------------

    async def build_report(self, payload: ReportRequest) -> ReportResponse:
        """«Верные цифры»: клики и конверсии по кампаниям/потокам/офферам."""
        path = "/report/build"
        data = await self._request("POST", path, body=payload.model_dump())
        return _parse_model(ReportResponse, data, method="POST", path=path)
