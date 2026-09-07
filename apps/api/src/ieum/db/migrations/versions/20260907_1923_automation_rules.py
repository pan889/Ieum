"""자동화 규칙: 조건-조치 (C9)

조건과 조치는 JSONB 다. 값의 모양은 서비스가 저장할 때 검증한다 —
DB 의 CHECK 로 두면 조치를 하나 늘릴 때마다 마이그레이션이 필요하고,
그 제약은 이미 저장된 행을 두고 뒤늦게 터진다.

Revision ID: a68ccca0839e
Revises: b99e8063aa70
Create Date: 2026-09-07 19:23:03.533385
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a68ccca0839e"
down_revision: str | None = "b99e8063aa70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "automation_rule",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("trigger", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "conditions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_automation_rule_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_automation_rule")),
        sa.UniqueConstraint("project_id", "name", name="uq_automation_rule_project_id_name"),
    )
    op.create_index(
        "ix_automation_rule_project_id", "automation_rule", ["project_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_automation_rule_project_id", table_name="automation_rule")
    op.drop_table("automation_rule")
