"""Базовый класс моделей и стабильные имена ограничений для миграций."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Без явных имён Alembic генерирует имена вида `operation_log_idempotency_key_key`,
# которые зависят от СУБД. С convention имена одинаковы в SQLite и Postgres,
# поэтому downgrade и будущие ALTER-миграции предсказуемы.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Общая база: единые имена constraint'ов и метаданные для Alembic."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
