"""Тесты старта: с неверным `.env` приложение падает сразу, а не на первом запросе."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest

from keitaro_wrapper.logging_setup import configure_logging
from keitaro_wrapper.main import create_app, lifespan

VALID_ENV = {
    "KEITARO_BASE_URL": "https://tlgk.host",
    "KEITARO_API_KEY": "test-key",
    "KEITARO_DOMAIN_ID": "1813",
    "KEITARO_GROUP_ID": "10332",
    "KEITARO_SOURCE_ID": "836",
}


def set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    """Готовит валидное окружение, точечно подменяя нужные переменные."""
    for name, value in {**VALID_ENV, **overrides}.items():
        monkeypatch.setenv(name, value)


@asynccontextmanager
async def running_app() -> AsyncIterator[httpx.AsyncClient]:
    """Поднимает приложение вместе с `lifespan` — так же, как это делает uvicorn."""
    app = create_app()
    async with lifespan(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


async def test_app_starts_with_valid_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch)

    async with running_app() as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_app_fails_fast_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пустой ключ — ошибка старта, иначе `/health` зелёный, а Keitaro отвечает 401."""
    set_env(monkeypatch, KEITARO_API_KEY="")

    with pytest.raises(RuntimeError, match="keitaro_api_key"):
        async with running_app():
            pass


async def test_app_fails_fast_on_http_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    set_env(monkeypatch, KEITARO_BASE_URL="http://tlgk.host")

    with pytest.raises(RuntimeError, match="https"):
        async with running_app():
            pass


async def test_startup_error_does_not_leak_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщение об ошибке перечисляет поля, но не печатает значения из `.env`."""
    set_env(monkeypatch, KEITARO_API_KEY="SECRET42", KEITARO_BASE_URL="http://tlgk.host")

    with pytest.raises(RuntimeError) as exc_info:
        async with running_app():
            pass

    assert "keitaro_base_url" in str(exc_info.value)
    assert "SECRET42" not in str(exc_info.value)


def test_configure_logging_applies_level() -> None:
    root = logging.getLogger()
    previous_level = root.level
    try:
        configure_logging("critical")

        assert root.level == logging.CRITICAL
        assert logging.getLogger("keitaro_wrapper").level == logging.CRITICAL
    finally:
        root.setLevel(previous_level)
        logging.getLogger("keitaro_wrapper").setLevel(logging.NOTSET)
