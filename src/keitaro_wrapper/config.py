"""Настройки приложения: читаются из окружения и `.env`."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, HttpUrl, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Хосты, для которых допустим http://: локальный мок Keitaro и отладка на своей машине
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

# Дефолт совпадает с docker-compose (порт 5433, чтобы не конфликтовать с локальным Postgres).
# Константа нужна и Alembic: миграции не должны требовать KEITARO_API_KEY, поэтому
# env.py берёт значение отсюда, а не инстанцирует полный Settings.
DEFAULT_DATABASE_URL = "postgresql+asyncpg://keitaro:keitaro@localhost:5433/keitaro_wrapper"


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
    # min_length=1 — пустой KEITARO_API_KEY= из .env отсекается на старте:
    # раньше приложение поднималось и падало с 401 на первом запросе к Keitaro
    keitaro_api_key: Annotated[SecretStr, Field(min_length=1)]
    keitaro_domain_id: int = Field(gt=0)
    keitaro_group_id: int = Field(gt=0)
    keitaro_source_id: int = Field(gt=0)
    keitaro_google_url: HttpUrl = HttpUrl("https://www.google.com")
    # Таймауты под веб-ручку: 10 с вместо 30, иначе зависший Keitaro держит
    # воркер до 90 с (30 с × 3 попытки + паузы). Для долгих вызовов (создание
    # кампании) значение поднимается через .env.
    keitaro_timeout: float = Field(default=10.0, gt=0, le=300)
    keitaro_retries: int = Field(default=1, ge=0, le=5)

    database_url: str = DEFAULT_DATABASE_URL
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"

    @field_validator("keitaro_api_key", mode="before")
    @classmethod
    def _strip_api_key(cls, value: object) -> object:
        """Убирает пробелы и переводы строк по краям ключа.

        Без этого значение `KEITARO_API_KEY="   "` проходило бы `min_length=1`
        и попадало в заголовок `Api-Key` с пробелами — Keitaro ответил бы 401.
        """
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("keitaro_base_url")
    @classmethod
    def _require_https(cls, value: HttpUrl) -> HttpUrl:
        """Запрещает http:// для боевого инстанса: ключ Api-Key шёл бы открытым текстом.

        Исключение — loopback-хосты: локальный мок Keitaro и отладка на своей
        машине, где трафик не покидает хост.
        """
        # HttpUrl отдаёт IPv6-хост в скобках ('[::1]'), поэтому скобки убираем
        host = (value.host or "").strip("[]")
        if value.scheme != "https" and host not in LOOPBACK_HOSTS:
            raise ValueError(
                "KEITARO_BASE_URL должен начинаться с https:// — по http ключ Api-Key "
                "передаётся открытым текстом"
            )
        return value

    @field_validator("log_level", mode="before")
    @classmethod
    def _normalize_log_level(cls, value: object) -> object:
        """`LOG_LEVEL=" INFO "` из `.env` приводится к виду, который ждёт Literal."""
        if isinstance(value, str):
            return value.strip().lower()
        return value

    @property
    def keitaro_api_root(self) -> str:
        """Корень Admin API: `<base>/admin_api/v1`."""
        return f"{str(self.keitaro_base_url).rstrip('/')}/admin_api/v1"


@lru_cache
def get_settings() -> Settings:
    """Кэшированный доступ к настройкам; первый вызов — в `lifespan` при старте.

    `lru_cache` держит одно значение на процесс, тесты сбрасывают его фикстурой
    `_reset_settings_cache` из `tests/conftest.py`.
    """
    return Settings()
