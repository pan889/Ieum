"""session: mfa_verified

Revision ID: c5b8f1d20e63
Revises: b4a7e0c91d52
Create Date: 2026-09-07 04:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c5b8f1d20e63"
down_revision: str | None = "b4a7e0c91d52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 기존 세션은 전부 False 다. 통과했는지 알 수 없으면 안 한 것으로 본다 —
    # 그 반대로 두면 업그레이드 순간 모든 세션이 민감 작업을 통과한다.
    op.add_column(
        "session",
        sa.Column("mfa_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("session", "mfa_verified")
