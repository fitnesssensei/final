"""Зависимости FastAPI: общие объекты приложения."""

from typing import Annotated

from fastapi import Depends, Request

from keitaro_wrapper.keitaro import KeitaroClient


def get_keitaro_client(request: Request) -> KeitaroClient:
    """Клиент создан в `lifespan` и закрывается при остановке приложения.

    Через `app.state` клиент переживает запросы и не создаёт новое соединение
    на каждый вызов — это тот же экземпляр, что поднят на старте.
    """
    return request.app.state.keitaro


KeitaroClientDep = Annotated[KeitaroClient, Depends(get_keitaro_client)]
