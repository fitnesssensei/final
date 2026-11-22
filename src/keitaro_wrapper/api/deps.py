"""Зависимости FastAPI: общие объекты приложения."""

import secrets
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.keitaro import KeitaroClient

# Basic-авторизация: браузер сам показывает окно входа на 401 с этим заголовком,
# а curl/скрипты передают `-u user:password`. auto_error=False — чтобы ответ
# 401 формировали мы (с понятным текстом), а не FastAPI по умолчанию.
_basic = HTTPBasic(auto_error=False)
AUTH_REALM = 'Basic realm="keitaro-wrapper"'


def get_keitaro_client(request: Request) -> KeitaroClient:
    """Клиент создан в `lifespan` и закрывается при остановке приложения.

    Через `app.state` клиент переживает запросы и не создаёт новое соединение
    на каждый вызов — это тот же экземпляр, что поднят на старте.
    """
    return request.app.state.keitaro


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Сессия БД на один запрос; транзакцию закрывает зависимость.

    `commit` остаётся за сервисом: ручка сначала пишет журнал, потом вызывает
    Keitaro, и фиксировать транзакцию должен код, который знает границу
    операции. Зависимость только гарантирует откат при исключении.
    """
    factory = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


KeitaroClientDep = Annotated[KeitaroClient, Depends(get_keitaro_client)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]


def require_auth(
    request: Request,
    credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
) -> None:
    """Проверяет HTTP Basic, если он настроен (`AUTH_USER`/`AUTH_PASSWORD`).

    Авторизация необязательная: без пары логин/пароль ручки открыты, как в
    локальном инструменте и в тестах. Если пара задана, без верных данных любой
    запрос к `/api/*` и `/ui/*` получает 401 — включая HTML-страницы, поэтому
    браузер показывает окно входа, а curl требует `-u user:password`.

    Настройки читаются из `app.state` (их кладёт `lifespan`). Если приложение
    поднято без `lifespan` (тесты, встраивание в чужой ASGI-стек), проверять
    нечего: uvicorn по умолчанию `lifespan` включает, поэтому в проде состояние
    есть всегда. Тот же `app.state.settings` используют TTL кэша и ретенция
    журнала — единая точка чтения настроек после старта.
    """
    settings = getattr(request.app.state, "settings", None)
    if settings is None or settings.auth_user is None or settings.auth_password is None:
        return

    expected_user = settings.auth_user
    expected_password = settings.auth_password.get_secret_value()
    # compare_digest — сравнение за постоянное время: обычное `==` по строке
    # позволяет подбирать пароль по времени ответа.
    authorized = (
        credentials is not None
        and secrets.compare_digest(credentials.username.encode(), expected_user.encode())
        and secrets.compare_digest(credentials.password.encode(), expected_password.encode())
    )
    if not authorized:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="требуется авторизация",
            headers={"WWW-Authenticate": AUTH_REALM},
        )


AuthDep = Depends(require_auth)
