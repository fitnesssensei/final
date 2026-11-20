"""Тесты скелета приложения: живость и сборка OpenAPI-схемы."""

import httpx

from keitaro_wrapper import __version__

# Фикстура `client` — общая, в tests/conftest.py


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
    # Literal["ok"] в схеме ответа: единственный возможный статус
    status_schema = schema["components"]["schemas"]["HealthResponse"]["properties"]["status"]
    assert status_schema["const"] == "ok"
