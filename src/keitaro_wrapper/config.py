"""Настройки приложения: читаются из окружения и `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LOG_LEVELS = {"critical", "error", "warning", "info", "debug"}


class Settings(BaseSettings):
    """Конфигурация инстанса Keitaro и локальной БД.

    Значения по умолчанию для БД рассчитаны на `docker compose up` (порт 5433,
    чтобы не конфликтовать с локальным Postgres на 5432).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    keitaro_base_url: HttpUrl
    keitaro_api_key: SecretStr
    keitaro_domain_id: int = Field(gt=0)
    keitaro_group_id: int = Field(gt=0)
    keitaro_source_id: int = Field(gt=0)
    keitaro_google_url: HttpUrl = HttpUrl("https://www.google.com")

    database_url: str = "postgresql+asyncpg://keitaro:keitaro@localhost:5433/keitaro_wrapper"
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"

    @field_validator("keitaro_base_url", "keitaro_google_url")
    @classmethod
    def _strip_trailing_slash(cls, value: HttpUrl) -> HttpUrl:
        return HttpUrl(str(value).rstrip("/"))

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def keitaro_api_root(self) -> str:
        """Корень Admin API: `<base>/admin_api/v1`."""
        return f"{str(self.keitaro_base_url).rstrip('/')}/admin_api/v1"


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам (валидация происходит при первом вызове)."""
    return Settings()
