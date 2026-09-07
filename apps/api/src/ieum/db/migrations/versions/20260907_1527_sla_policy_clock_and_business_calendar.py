"""SLA 정책·클럭·업무 달력 (C4, C5)

**`sla_clock.target_at` 을 저장한다.** 매번 계산하면 관리자가 달력이나 정책을
고치는 순간 지난 티켓의 위반 여부가 조용히 바뀐다 — 어제 지킨 약속이 오늘
깨진 것이 된다.

`ix_sla_clock_pending` 은 부분 인덱스다. 스윕이 "안 끝났고 안 알린" 것만
훑으므로, 그 조건을 인덱스에 넣지 않으면 티켓이 쌓일수록 스윕이 테이블
전체를 읽는다.

Revision ID: 629fe89858b5
Revises: 894b991e7319
Create Date: 2026-09-07 15:27:56.347323
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "629fe89858b5"
down_revision: str | None = "894b991e7319"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "business_calendar",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "working_hours",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "holidays", postgresql.ARRAY(sa.String(length=10)), server_default="{}", nullable=False
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_business_calendar")),
        sa.UniqueConstraint("name", name="uq_business_calendar_name"),
    )
    op.create_table(
        "sla_policy",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("metric", sa.String(length=20), nullable=False),
        sa.Column("calendar_id", sa.Uuid(), nullable=False),
        sa.Column(
            "goals", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "metric IN ('first_response', 'resolution')",
            name=op.f("ck_sla_policy_sla_policy_metric"),
        ),
        sa.ForeignKeyConstraint(
            ["calendar_id"],
            ["business_calendar.id"],
            name=op.f("fk_sla_policy_calendar_id_business_calendar"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_sla_policy_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sla_policy")),
        sa.UniqueConstraint("project_id", "name", name="uq_sla_policy_project_id_name"),
    )
    op.create_index("ix_sla_policy_calendar_id", "sla_policy", ["calendar_id"], unique=False)
    op.create_index("ix_sla_policy_project_id", "sla_policy", ["project_id"], unique=False)
    op.create_table(
        "sla_clock",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("policy_id", sa.Uuid(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("paused_seconds", sa.Integer(), nullable=False),
        sa.Column("paused_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("breached_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["issue_id"], ["issue.id"], name=op.f("fk_sla_clock_issue_id_issue"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"],
            ["sla_policy.id"],
            name=op.f("fk_sla_clock_policy_id_sla_policy"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("issue_id", "policy_id", name=op.f("pk_sla_clock")),
    )
    op.create_index(
        "ix_sla_clock_pending",
        "sla_clock",
        ["target_at"],
        unique=False,
        postgresql_where=sa.text("completed_at IS NULL AND breached_at IS NULL"),
    )
    op.create_index("ix_sla_clock_policy_id", "sla_clock", ["policy_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_sla_clock_policy_id", table_name="sla_clock")
    op.drop_index(
        "ix_sla_clock_pending",
        table_name="sla_clock",
        postgresql_where=sa.text("completed_at IS NULL AND breached_at IS NULL"),
    )
    op.drop_table("sla_clock")
    op.drop_index("ix_sla_policy_project_id", table_name="sla_policy")
    op.drop_index("ix_sla_policy_calendar_id", table_name="sla_policy")
    op.drop_table("sla_policy")
    op.drop_table("business_calendar")
