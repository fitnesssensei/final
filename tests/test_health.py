"""Тесты скелета приложения: живость и сборка OpenAPI-схемы."""

from collections.abc import AsyncIterator

import httpx
import pytest

from keitaro_wrapper import __version__
from keitaro_wrapper.main import create_app


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


async def test_health_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_openapi_schema_contains_health(client: httpx.AsyncClient) -> None:
    response = await client.get("/openapi.json")

    assert response.status_code == 200
    schema = response.json()
    assert schema["info"]["version"] == __version__
    assert "/health" in schema["paths"]
