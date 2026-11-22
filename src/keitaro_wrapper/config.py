"""Настройки приложения: читаются из окружения и `.env`."""

from functools import lru_cache
from typing import Annotated, Literal, Self

from pydantic import Field, HttpUrl, SecretStr, field_validator, model_validator
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
    # Таймаут одного запроса к Keitaro. 60 с — по факту живого прогона
    # 19.11.2026: `POST /streams` на инстансе не ответил за 10 с, и создание
    # кампании падало в 502 (ReadTimeout) с компенсацией. 10 с оставалось
    # слишком мало для записи; чтение укладывается и в меньший срок.
    keitaro_timeout: float = Field(default=60.0, gt=0, le=300)
    keitaro_retries: int = Field(default=1, ge=0, le=5)

    database_url: str = DEFAULT_DATABASE_URL
    log_level: Literal["critical", "error", "warning", "info", "debug"] = "info"

    # --- Доступ к API и интерфейсу (необязательно) -------------------------------
    # HTTP Basic для /api/* и /ui/*. Пустая пара — авторизация выключена
    # (дефолт: локальный инструмент). Половина пары — ошибка настройки:
    # «почти включённая» авторизация опаснее её отсутствия.
    auth_user: str | None = None
    auth_password: SecretStr | None = None

    # --- Локальная БД: свежесть кэша и срок жизни журнала ------------------------
    # Сколько секунд справочники (кампании, офферы, домены…) считаются свежими.
    # 0 — TTL выключен: кэш обновляется только когда он пуст или явно (?refresh=1).
    reference_cache_ttl: int = Field(default=900, ge=0, le=86400)
    # Записи журнала операций старше N дней удаляются на старте приложения.
    # 0 — не удалять (журнал растёт бесконечно).
    operations_retention_days: int = Field(default=7, ge=0, le=3650)

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

    @field_validator("auth_user", "auth_password", mode="before")
    @classmethod
    def _empty_auth_means_disabled(cls, value: object) -> object:
        """`AUTH_USER=` в `.env` — это «выключено», а не пустой логин.

        Без нормализации пустая строка принималась бы как валидное значение,
        и авторизация включалась бы с пустым именем — то есть не защищала бы.
        """
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def _auth_pair_is_complete(self) -> Self:
        """Логин и пароль задаются только вместе: половина пары — ошибка старта."""
        if (self.auth_user is None) != (self.auth_password is None):
            raise ValueError("AUTH_USER и AUTH_PASSWORD задаются вместе или не задаются вовсе")
        return self

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
