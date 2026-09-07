"""SLA 에스컬레이션: 정책의 규칙과 클럭의 실행 표시 (C5)

`goal_seconds` 는 기존 행에 0 으로 들어간다. 목표를 모르는 클럭은
에스컬레이션 대상이 아니게 되고(`sla.consumed_percent` 가 0 을 돌려준다),
새로 걸리는 클럭부터 값이 채워진다 — `target_at` 에서 거꾸로 계산하려면
달력이 필요해서 SQL 로 채울 수 없다.

Revision ID: bbf2793781b0
Revises: a383bb01ebe7
Create Date: 2026-09-07 17:14:50.984455
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bbf2793781b0"
down_revision: str | None = "a383bb01ebe7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sla_clock", sa.Column("goal_seconds", sa.Integer(), server_default="0", nullable=False)
    )
    op.add_column(
        "sla_clock",
        sa.Column(
            "escalated", postgresql.ARRAY(sa.String(length=40)), server_default="{}", nullable=False
        ),
    )
    op.create_index(
        "ix_sla_clock_running",
        "sla_clock",
        ["target_at"],
        unique=False,
        postgresql_where=sa.text("completed_at IS NULL AND paused_at IS NULL"),
    )
    op.add_column(
        "sla_policy",
        sa.Column(
            "escalations",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sla_policy", "escalations")
    op.drop_index(
        "ix_sla_clock_running",
        table_name="sla_clock",
        postgresql_where=sa.text("completed_at IS NULL AND paused_at IS NULL"),
    )
    op.drop_column("sla_clock", "escalated")
    op.drop_column("sla_clock", "goal_seconds")
