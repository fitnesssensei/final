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
        self._api_key = api_key
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
        """Тело ответа как JSON; пустое или не-JSON тело — None."""
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

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
        payload = self._json_or_none(response)
        if response.is_success:
            return payload

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
        data = await self._request("GET", "/campaigns")
        return [Campaign.model_validate(item) for item in data or []]

    async def get_campaign(self, campaign_id: int) -> Campaign:
        data = await self._request("GET", f"/campaigns/{campaign_id}")
        return Campaign.model_validate(data or {})

    async def create_campaign(self, payload: CampaignCreate) -> Campaign:
        data = await self._request("POST", "/campaigns", body=payload.model_dump(exclude_none=True))
        return Campaign.model_validate(data or {})

    async def delete_campaign(self, campaign_id: int) -> None:
        """Архивирует кампанию: Keitaro не удаляет её физически."""
        await self._request("DELETE", f"/campaigns/{campaign_id}")

    async def list_campaign_streams(self, campaign_id: int) -> list[Stream]:
        data = await self._request("GET", f"/campaigns/{campaign_id}/streams")
        return [Stream.model_validate(item) for item in data or []]

    async def create_stream(self, payload: StreamCreate) -> Stream:
        body = payload.model_dump(by_alias=True, exclude_none=True)
        data = await self._request("POST", "/streams", body=body)
        return Stream.model_validate(data or {})

    async def update_stream(self, stream_id: int, payload: StreamCreate) -> Stream:
        body = payload.model_dump(by_alias=True, exclude_none=True)
        data = await self._request("PUT", f"/streams/{stream_id}", body=body)
        return Stream.model_validate(data or {})

    # --- Справочники -------------------------------------------------------

    async def list_offers(self) -> list[Offer]:
        data = await self._request("GET", "/offers")
        return [Offer.model_validate(item) for item in data or []]

    async def get_offer(self, offer_id: int) -> Offer:
        data = await self._request("GET", f"/offers/{offer_id}")
        return Offer.model_validate(data or {})

    async def list_domains(self) -> list[Domain]:
        data = await self._request("GET", "/domains")
        return [Domain.model_validate(item) for item in data or []]

    async def list_groups(self, group_type: GroupType = "campaigns") -> list[Group]:
        """`type` обязателен: без него живой инстанс отвечает 400."""
        data = await self._request("GET", "/groups", params={"type": group_type})
        return [Group.model_validate(item) for item in data or []]

    async def list_traffic_sources(self) -> list[TrafficSource]:
        data = await self._request("GET", "/traffic_sources")
        return [TrafficSource.model_validate(item) for item in data or []]

    # --- Отчёты ------------------------------------------------------------

    async def build_report(self, payload: ReportRequest) -> ReportResponse:
        """«Верные цифры»: клики и конверсии по кампаниям/потокам/офферам."""
        data = await self._request("POST", "/report/build", body=payload.model_dump())
        return ReportResponse.model_validate(data or {})
