"""drop legacy geo_map

Таблица `geo_map` осталась от первой версии кэша (ISO-2 → название страны в
Keitaro) и приложением не использовалась: маппинг так и не подключили, таблица
всегда пустая. Модель `GeoMap` удалена, поэтому удаляем и саму таблицу.

Revision ID: c4f7a1d2b3e5
Revises: 8b1e4c7d2a90
Create Date: 2026-11-21 15:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4f7a1d2b3e5"
down_revision: str | None = "8b1e4c7d2a90"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Удаляет неиспользуемую таблицу соответствий гео."""
    op.drop_table("geo_map")


def downgrade() -> None:
    """Возвращает таблицу в том же виде, в каком её создала первая миграция."""
    op.create_table(
        "geo_map",
        sa.Column("code", sa.String(length=2), nullable=False),
        sa.Column("keitaro_country", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_geo_map")),
        sa.UniqueConstraint("keitaro_country", name=op.f("uq_geo_map_keitaro_country")),
    )
