"""SLA 일시정지 상태 (C4)

상태 **id** 로 담는다. category 로 둘 수 없다(워크플로우에 "고객 답변 대기"
category 가 없다) 그리고 이름으로 두면 관리자가 상태 이름을 바꾸는 순간
조용히 안 멈춘다.

앞 마이그레이션에 넣지 않고 따로 두는 이유: 그건 이미 푸시됐다. 적용된
마이그레이션을 고치면 이미 올린 사람과 안 올린 사람의 스키마가 갈라진다.

Revision ID: a383bb01ebe7
Revises: 629fe89858b5
Create Date: 2026-09-07 15:31:22.708880
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a383bb01ebe7"
down_revision: str | None = "629fe89858b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sla_policy",
        sa.Column(
            "pause_state_ids", postgresql.ARRAY(sa.Uuid()), server_default="{}", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("sla_policy", "pause_state_ids")
