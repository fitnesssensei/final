"""HTML-интерфейс: создаватель кампании и редактор офферов.

Серверный рендеринг без JS-фреймворка: формы отправляются обычным POST, а
ошибки возвращаются той же страницей с понятным текстом. Это даёт тестируемые
действия через `httpx` и не требует отдельной сборки фронтенда.

Идемпотентность: каждая форма несёт скрытый `Idempotency-Key`, сгенерированный
на рендер. Повторное нажатие/сетевой ретрай не создаст вторую кампанию и не
добавит оффер дважды.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Annotated, Any, get_args

from fastapi import APIRouter, Form, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from keitaro_wrapper.api.deps import KeitaroClientDep, SessionDep
from keitaro_wrapper.api.errors import describe_user_error
from keitaro_wrapper.config import get_settings
from keitaro_wrapper.keitaro import KeitaroError
from keitaro_wrapper.services.campaigns import CampaignRequest, CampaignResult, create_campaign
from keitaro_wrapper.services.editor import (
    DEFAULT_INTERVAL,
    AddOfferRequest,
    CampaignDetail,
    ReportInterval,
    add_offer,
    get_campaign_detail,
    remove_offer,
)
from keitaro_wrapper.services.reference import (
    CreatorOptions,
    DirectoryOption,
    campaign_options,
    creator_options,
    offer_options,
    refresh_directory,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ui", tags=["ui"])
root_router = APIRouter(tags=["ui"])
templates = Jinja2Templates(directory=Path(__file__).resolve().parents[2] / "templates")

REPORT_INTERVALS = frozenset(get_args(ReportInterval))
ACTION_NOTICES = {
    "added": "Оффер добавлен, веса пересчитаны",
    "removed": "Оффер удалён, веса пересчитаны",
}


def _key() -> str:
    """Ключ идемпотентности на одно действие формы."""
    return secrets.token_hex(16)


def _interval(value: str) -> ReportInterval:
    """Невалидный hidden-параметр не должен ломать страницу — берём дефолт."""
    return value if value in REPORT_INTERVALS else DEFAULT_INTERVAL  # type: ignore[return-value]


def _int_or_none(value: str) -> int | None:
    """Пустой select → None (сервис возьмёт значение из `.env`)."""
    return int(value) if value.strip() else None


def _with_default(
    options: list[DirectoryOption], default_id: int | None, label: str
) -> list[DirectoryOption]:
    """Добавляет значение из `.env`, если его нет в кэше — select не пустует."""
    if default_id is None or any(option.id == default_id for option in options):
        return options
    return [DirectoryOption(id=default_id, name=f"{label} {default_id} (из .env)"), *options]


def _render(
    request: Request,
    template: str,
    context: dict[str, Any],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    """Рендерит шаблон и запрещает кэширование страниц с ключами форм."""
    response = templates.TemplateResponse(
        request=request, name=template, context=context, status_code=status_code
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _error(exc: Exception) -> tuple[int, str] | None:
    """Известная ошибка → (статус, текст); неизвестная пусть всплывает.

    Полный текст ошибки трекера (метод, путь, поле, ответ) пишем в лог: на
    странице показывается только понятная человеку причина, а разбираться с
    контрактом Admin API удобнее по логу.
    """
    described = describe_user_error(exc)
    if described is not None and isinstance(exc, KeitaroError):
        logger.warning("Keitaro (%s): %s", exc.status or "?", exc)
    return described


def _validation_message(exc: ValidationError) -> str:
    """Первая ошибка формы без pydantic-префикса `Value error, `."""
    error = exc.errors()[0]
    message = str(error.get("msg", "невалидные данные"))
    return message.removeprefix("Value error, ")


def _creator_form(
    *,
    name: str = "",
    geo: str = "",
    offer_id: int | None = None,
    domain_id: int | None = None,
    group_id: int | None = None,
    traffic_source_id: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    return {
        "name": name,
        "geo": geo,
        "offer_id": offer_id,
        "domain_id": domain_id or settings.keitaro_domain_id,
        "group_id": group_id or settings.keitaro_group_id,
        "traffic_source_id": traffic_source_id or settings.keitaro_source_id,
        "idempotency_key": idempotency_key or _key(),
    }


@root_router.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    """Корень ведёт на создаватель."""
    return RedirectResponse("/ui/creator", status_code=303)


async def _creator_page(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
    form: dict[str, Any],
    *,
    error: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    """Общий рендер создавателя: селекты из кэша + сохранённые значения формы."""
    try:
        options = await creator_options(session, client)
    except Exception as exc:  # noqa: BLE001 - известные ошибки показываем, остальные пробрасываем
        described = _error(exc)
        if described is None:
            raise
        if error is None:
            error, status_code = described[1], described[0]
        options = CreatorOptions()

    settings = get_settings()
    options.domains = _with_default(options.domains, settings.keitaro_domain_id, "домен")
    options.groups = _with_default(options.groups, settings.keitaro_group_id, "группа")
    options.traffic_sources = _with_default(
        options.traffic_sources, settings.keitaro_source_id, "источник"
    )
    if form["offer_id"] is None:
        form["offer_id"] = next(
            (offer.id for offer in options.offers if offer.state in (None, "active")), None
        )
    if error is not None:
        # После любой ошибки новой отправке нужен новый ключ: операция могла
        # уже попасть в журнал как failed, и старый ключ даст 409 вместо повтора.
        form["idempotency_key"] = _key()
    has_active_offers = any(offer.state in (None, "active") for offer in options.offers)

    return _render(
        request,
        "creator.html",
        {
            "form": form,
            "options": options,
            "has_active_offers": has_active_offers,
            "error": error,
        },
        status_code=status_code,
    )


@router.get(
    "/creator",
    response_class=HTMLResponse,
    summary="Экран создавателя кампании",
)
async def creator_page(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
) -> HTMLResponse:
    """Форма создания: домен, группа, источник и оффер — из кэша справочников."""
    return await _creator_page(request, session, client, _creator_form())


@router.post(
    "/creator",
    response_class=HTMLResponse,
    response_model=None,
    summary="Создать кампанию из формы",
)
async def creator_submit(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
    name: Annotated[str, Form()] = "",
    geo: Annotated[str, Form()] = "",
    offer_id: Annotated[str, Form()] = "",
    domain_id: Annotated[str, Form()] = "",
    group_id: Annotated[str, Form()] = "",
    traffic_source_id: Annotated[str, Form()] = "",
    idempotency_key: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Валидирует форму, создаёт кампанию и открывает её в редакторе."""
    try:
        form = _creator_form(
            name=name,
            geo=geo,
            domain_id=_int_or_none(domain_id),
            group_id=_int_or_none(group_id),
            traffic_source_id=_int_or_none(traffic_source_id),
            idempotency_key=idempotency_key.strip() or None,
        )
    except ValueError:
        form = _creator_form(name=name, geo=geo, idempotency_key=idempotency_key.strip() or None)
        return await _creator_page(
            request,
            session,
            client,
            form,
            error="ID справочника должен быть числом",
            status_code=422,
        )
    try:
        form["offer_id"] = _int_or_none(offer_id)
        if form["offer_id"] is None:
            return await _creator_page(
                request,
                session,
                client,
                form,
                error="выберите оффер",
                status_code=422,
            )
        payload = CampaignRequest(
            name=name,
            geo=geo,
            offer_id=form["offer_id"] or 0,
            domain_id=form["domain_id"],
            group_id=form["group_id"],
            traffic_source_id=form["traffic_source_id"],
        )
    except (ValidationError, ValueError) as exc:
        message = _validation_message(exc) if isinstance(exc, ValidationError) else "выберите оффер"
        return await _creator_page(request, session, client, form, error=message, status_code=422)

    try:
        result: CampaignResult = await create_campaign(
            session,
            client,
            get_settings(),
            payload,
            idempotency_key=form["idempotency_key"],
        )
    except Exception as exc:  # noqa: BLE001 - известные ошибки показываем страницей
        described = _error(exc)
        if described is None:
            raise
        return await _creator_page(
            request,
            session,
            client,
            form,
            error=described[1],
            status_code=described[0],
        )

    # Селектор редактора берётся из кэша: без обновления только что созданная
    # кампания не появится в списке после перехода по навигации.
    try:
        await refresh_directory(session, client, "campaigns")
        await session.commit()
    except Exception:  # noqa: BLE001 - кампания уже создана, кэш не должен ломать редирект
        logger.warning("не удалось обновить кэш кампаний после создания", exc_info=True)
        await session.rollback()

    return RedirectResponse(
        f"/ui/editor?campaign_id={result.campaign_id}&created=1", status_code=303
    )


async def _editor_page(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
    *,
    campaign_id: int | None,
    interval: ReportInterval = DEFAULT_INTERVAL,
    refresh: bool = False,
    error: str | None = None,
    status_code: int = 200,
    notice: str | None = None,
) -> HTMLResponse:
    """Общий рендер редактора: селектор кампаний, потоки, офферы и цифры."""
    page_error, page_status = error, status_code
    try:
        campaigns = await campaign_options(session, client, refresh=refresh)
    except Exception as exc:  # noqa: BLE001 - известные ошибки показываем страницей
        described = _error(exc)
        if described is None:
            raise
        campaigns = []
        if page_error is None:
            page_error, page_status = described[1], described[0]

    selected = campaign_id or (campaigns[0].id if campaigns else None)
    detail: CampaignDetail | None = None
    if selected is not None:
        try:
            detail = await get_campaign_detail(session, client, selected, interval=interval)
        except Exception as exc:  # noqa: BLE001
            described = _error(exc)
            if described is None:
                raise
            if page_error is None:
                page_error, page_status = described[1], described[0]

    try:
        offers = await offer_options(session, client, refresh=refresh)
    except Exception as exc:  # noqa: BLE001
        described = _error(exc)
        if described is None:
            raise
        offers = []
        if page_error is None:
            page_error, page_status = described[1], described[0]

    if detail is not None and not any(item.id == detail.campaign_id for item in campaigns):
        campaigns.insert(
            0,
            DirectoryOption(id=detail.campaign_id, name=detail.name, state=detail.state),
        )

    used_offer_ids = (
        {offer.offer_id for stream in detail.streams for offer in stream.offers}
        if detail is not None
        else set()
    )
    # Ключ на действие (поток, оффер): один оффер может стоять в нескольких
    # потоках, и удаление из каждого — отдельная операция со своим ключом.
    delete_keys = {
        (stream.id, offer.offer_id): _key()
        for stream in (detail.streams if detail is not None else [])
        for offer in stream.offers
    }
    available_offers = [
        offer
        for offer in offers
        if offer.id not in used_offer_ids and offer.state in (None, "active")
    ]
    return _render(
        request,
        "editor.html",
        {
            "campaigns": campaigns,
            "selected_campaign_id": selected,
            "detail": detail,
            "offers": offers,
            "available_offers": available_offers,
            "used_offer_ids": used_offer_ids,
            "interval": interval,
            "error": page_error,
            "notice": notice,
            "add_key": _key(),
            "delete_keys": delete_keys,
        },
        status_code=page_status,
    )


@router.get(
    "/editor",
    response_class=HTMLResponse,
    summary="Экран редактора кампании",
)
async def editor_page(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
    campaign_id: Annotated[int | None, Query(gt=0, description="ID кампании")] = None,
    interval: Annotated[ReportInterval, Query(description="Диапазон цифр")] = DEFAULT_INTERVAL,
    refresh: Annotated[int, Query(ge=0, le=1, description="1 — обновить кэш")] = 0,
    created: Annotated[int, Query(ge=0, le=1, description="Показать «кампания создана»")] = 0,
    action: Annotated[str | None, Query(description="added/removed")] = None,
) -> HTMLResponse:
    """Показывает кампанию, потоки, веса и цифры; действия — отдельными POST."""
    notice = ACTION_NOTICES.get(action or "")
    if created:
        notice = "Кампания создана. Кампания и два потока открыты в редакторе."
    return await _editor_page(
        request,
        session,
        client,
        campaign_id=campaign_id,
        interval=interval,
        refresh=bool(refresh),
        notice=notice,
    )


@router.post(
    "/editor/{campaign_id}/offers",
    response_class=HTMLResponse,
    response_model=None,
    summary="Добавить оффер из формы редактора",
)
async def editor_add_offer(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
    campaign_id: Annotated[int, PathParam(gt=0, description="ID кампании")],
    offer_id: Annotated[str, Form()] = "",
    idempotency_key: Annotated[str, Form()] = "",
    interval: Annotated[str, Form()] = DEFAULT_INTERVAL,
) -> HTMLResponse | RedirectResponse:
    """Добавляет оффер и возвращает в редактор с сообщением об успехе."""
    period = _interval(interval)
    try:
        # Проверяет и число, и `gt=0`: голый `int()` пропускает `0` и `-5`,
        # а модель отсекает их на входе и даёт понятный текст для страницы.
        offer_request = AddOfferRequest(offer_id=int(offer_id))
    except ValidationError:
        # `ValidationError` — наследник `ValueError`, поэтому ловим его первым:
        # иначе нечисловое и неположительное значения слились бы в один текст.
        return await _editor_page(
            request,
            session,
            client,
            campaign_id=campaign_id,
            interval=period,
            error="ID оффера должен быть больше нуля",
            status_code=422,
        )
    except ValueError:
        return await _editor_page(
            request,
            session,
            client,
            campaign_id=campaign_id,
            interval=period,
            error="выберите оффер для добавления",
            status_code=422,
        )

    try:
        await add_offer(
            session,
            client,
            campaign_id,
            offer_request,
            idempotency_key=idempotency_key.strip() or None,
        )
    except Exception as exc:  # noqa: BLE001 - известные ошибки показываем страницей
        described = _error(exc)
        if described is None:
            raise
        return await _editor_page(
            request,
            session,
            client,
            campaign_id=campaign_id,
            interval=period,
            error=described[1],
            status_code=described[0],
        )

    return RedirectResponse(
        f"/ui/editor?campaign_id={campaign_id}&action=added&interval={period}",
        status_code=303,
    )


@router.post(
    "/editor/{campaign_id}/offers/{offer_id}/delete",
    response_class=HTMLResponse,
    response_model=None,
    summary="Удалить оффер из формы редактора",
)
async def editor_remove_offer(
    request: Request,
    session: SessionDep,
    client: KeitaroClientDep,
    campaign_id: Annotated[int, PathParam(gt=0, description="ID кампании")],
    offer_id: Annotated[int, PathParam(gt=0, description="ID оффера")],
    idempotency_key: Annotated[str, Form()] = "",
    interval: Annotated[str, Form()] = DEFAULT_INTERVAL,
    stream_id: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Удаляет оффер (POST вместо DELETE: HTML-форма) и показывает веса заново."""
    period = _interval(interval)
    try:
        # Поток из формы: оффер может стоять в нескольких потоках, поэтому
        # без него действие было бы неоднозначным. Пустое значение — старый
        # вариант формы, тогда поток выберет сервис.
        parsed_stream_id = int(stream_id) if stream_id.strip() else None
    except ValueError:
        return await _editor_page(
            request,
            session,
            client,
            campaign_id=campaign_id,
            interval=period,
            error="некорректный поток кампании — обновите страницу",
            status_code=422,
        )

    try:
        await remove_offer(
            session,
            client,
            campaign_id,
            offer_id,
            idempotency_key=idempotency_key.strip() or None,
            stream_id=parsed_stream_id,
        )
    except Exception as exc:  # noqa: BLE001 - известные ошибки показываем страницей
        described = _error(exc)
        if described is None:
            raise
        return await _editor_page(
            request,
            session,
            client,
            campaign_id=campaign_id,
            interval=period,
            error=described[1],
            status_code=described[0],
        )

    return RedirectResponse(
        f"/ui/editor?campaign_id={campaign_id}&action=removed&interval={period}",
        status_code=303,
    )
