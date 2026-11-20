"""Общие фикстуры тестов."""

import os

import pytest

from keitaro_wrapper.config import get_settings


@pytest.fixture(autouse=True)
def _clear_keitaro_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Убирает переменные KEITARO_* из окружения на время теста.

    `_env_file=None` отключает только `.env`, но не `os.environ`: без этой
    фикстуры тесты настроек зависят от машины разработчика. Регистр не важен —
    pydantic-settings по умолчанию сравнивает имена без учёта регистра.
    """
    for name in [key for key in os.environ if key.upper().startswith("KEITARO_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _reset_settings_cache() -> None:
    """Сбрасывает кэш `get_settings()` до и после теста.

    `get_settings` кэширован через `lru_cache`, а тесты меняют окружение:
    без сброса настройки предыдущего теста (в том числе невалидные) протекали
    бы в следующий.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
