"""Тесты настроек: значения читаются из окружения, ошибки видны на старте."""

import pytest
from pydantic import ValidationError

from keitaro_wrapper.config import Settings

BASE_KWARGS = {
    "keitaro_base_url": "https://tlgk.host",
    "keitaro_api_key": "test-key",
    "keitaro_domain_id": 1813,
    "keitaro_group_id": 10332,
    "keitaro_source_id": 836,
}


def make_settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **{**BASE_KWARGS, **overrides})


def test_api_root_has_no_double_slash() -> None:
    settings = make_settings(keitaro_base_url="https://tlgk.host/")

    assert settings.keitaro_api_root == "https://tlgk.host/admin_api/v1"


def test_api_key_is_masked() -> None:
    settings = make_settings()

    assert "test-key" not in repr(settings)
    assert settings.keitaro_api_key.get_secret_value() == "test-key"


def test_log_level_is_normalized() -> None:
    assert make_settings(log_level=" DEBUG ").log_level == "debug"


def test_invalid_log_level_fails_on_start() -> None:
    with pytest.raises(ValidationError):
        make_settings(log_level="verbose")


def test_missing_required_env_fails_on_start() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, keitaro_base_url="https://tlgk.host")

    assert "keitaro_api_key" in str(exc_info.value)


def test_default_google_url_and_database_url() -> None:
    settings = make_settings()

    assert str(settings.keitaro_google_url).rstrip("/") == "https://www.google.com"
    assert settings.database_url.endswith("/keitaro_wrapper")
    assert settings.keitaro_api_root == "https://tlgk.host/admin_api/v1"
