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


def test_missing_required_env_reports_all_fields() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)

    # Сравниваем точный набор отсутствующих полей, а не текст ошибки:
    # pydantic печатает в текст весь input_value, поэтому проверка подстрокой
    # проходит даже тогда, когда поле фактически пришло из окружения.
    missing = {err["loc"][0] for err in exc_info.value.errors() if err["type"] == "missing"}
    assert missing == {
        "keitaro_base_url",
        "keitaro_api_key",
        "keitaro_domain_id",
        "keitaro_group_id",
        "keitaro_source_id",
    }


def test_missing_api_key_is_reported_by_field_name() -> None:
    kwargs = {key: value for key, value in BASE_KWARGS.items() if key != "keitaro_api_key"}

    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None, **kwargs)

    # Ровно одна ошибка и ровно по нужному полю: тест поймает и лишние ошибки,
    # и ситуацию, когда поле перестало быть обязательным.
    assert [(err["loc"], err["type"]) for err in exc_info.value.errors()] == [
        (("keitaro_api_key",), "missing")
    ]


def test_default_google_url_and_database_url() -> None:
    settings = make_settings()

    assert str(settings.keitaro_google_url).rstrip("/") == "https://www.google.com"
    assert settings.database_url.endswith("/keitaro_wrapper")
    assert settings.keitaro_api_root == "https://tlgk.host/admin_api/v1"


def test_empty_api_key_is_rejected() -> None:
    """Пустой ключ из `.env` — ошибка на старте, а не 401 на первом запросе к Keitaro."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(keitaro_api_key="")

    assert [err["loc"] for err in exc_info.value.errors()] == [("keitaro_api_key",)]


def test_whitespace_only_api_key_is_rejected() -> None:
    """Пробелы вместо ключа — та же ошибка: пользователь забыл вписать значение."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(keitaro_api_key="   ")

    assert [err["loc"] for err in exc_info.value.errors()] == [("keitaro_api_key",)]


def test_api_key_is_stripped() -> None:
    """Пробелы и перевод строки по краям не должны попадать в заголовок Api-Key."""
    settings = make_settings(keitaro_api_key="  secret-key  \n")

    assert settings.keitaro_api_key.get_secret_value() == "secret-key"


def test_http_base_url_is_rejected() -> None:
    """По http заголовок Api-Key уходит открытым текстом — такие настройки запрещены."""
    with pytest.raises(ValidationError) as exc_info:
        make_settings(keitaro_base_url="http://tlgk.host")

    assert [err["loc"] for err in exc_info.value.errors()] == [("keitaro_base_url",)]


def test_http_base_url_is_allowed_for_loopback() -> None:
    """Локальный мок Keitaro слушает http://127.0.0.1 — за пределы машины ключ не уходит."""
    for loopback_url in ("http://127.0.0.1:8080", "http://localhost:8000"):
        settings = make_settings(keitaro_base_url=loopback_url)

        assert str(settings.keitaro_base_url).startswith("http://")


def test_http_ipv6_loopback_is_allowed() -> None:
    """HttpUrl отдаёт хост IPv6 в скобках ('[::1]') — исключение должно работать и для него."""
    settings = make_settings(keitaro_base_url="http://[::1]:8080")

    assert str(settings.keitaro_base_url).startswith("http://")
