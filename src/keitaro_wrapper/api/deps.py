"""Зависимости FastAPI: общие объекты приложения."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from keitaro_wrapper.keitaro import KeitaroClient


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
