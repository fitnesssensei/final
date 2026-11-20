"""store operation result

Revision ID: 8b1e4c7d2a90
Revises: 0e1c7bf97570
Create Date: 2026-11-20 12:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b1e4c7d2a90"
down_revision: str | None = "0e1c7bf97570"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Снимок успешного ответа для идемпотентного повтора (шаг 4)."""
    op.add_column("operation_log", sa.Column("result", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("operation_log", "result")
