"""Общие фикстуры тестов."""

import os

import pytest


@pytest.fixture(autouse=True)
def _clear_keitaro_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убирает переменные KEITARO_* из окружения на время теста.

    `_env_file=None` отключает только `.env`, но не `os.environ`: без этой
    фикстуры тесты настроек зависят от машины разработчика. Регистр не важен —
    pydantic-settings по умолчанию сравнивает имена без учёта регистра.
    """
    for name in [key for key in os.environ if key.upper().startswith("KEITARO_")]:
        monkeypatch.delenv(name, raising=False)
