"""role: require_mfa

Revision ID: b4a7e0c91d52
Revises: 9e2d41b7c058
Create Date: 2026-09-07 03:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b4a7e0c91d52"
down_revision: str | None = "9e2d41b7c058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기존 역할은 강제하지 않는다. 켜는 것은 관리자의 결정이지, 업그레이드가
    # 대신 내릴 결정이 아니다 — 어느 날 갑자기 전원이 등록 화면에 갇힌다.
    op.add_column(
        "role",
        sa.Column("require_mfa", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("role", "require_mfa")
