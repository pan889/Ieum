"""page_task — 본문의 태스크를 모아 보기 위한 유도 표 (B12, M5)

정본은 본문의 마크다운이다(ADR-0008). 이 표는 검색 색인과 같은 성질로,
문서를 저장할 때 본문에서 다시 만들어진다 — 직접 고치면 다음 저장에서
사라진다.

**기존 문서의 태스크는 여기서 채우지 않는다.** 마이그레이션이 마크다운
파서를 들고 오면 그 파서의 그때 모양이 데이터에 굳는다. 문서를 다시 저장할
때 자연히 채워지고, 그전까지 "내 할 일" 에 안 뜨는 것은 잘못된 값이 뜨는
것보다 낫다.

Revision ID: 59c965f75275
Revises: 152dd79bde6a
Create Date: 2026-09-08 07:39:38.823160
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "59c965f75275"
down_revision: str | None = "152dd79bde6a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "page_task",
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("done", sa.Boolean(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["assignee_id"],
            ["user.id"],
            name=op.f("fk_page_task_assignee_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["page_id"], ["page.id"], name=op.f("fk_page_task_page_id_page"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page_task")),
        sa.UniqueConstraint("page_id", "line", name="uq_page_task_page_id_line"),
    )
    op.create_index(
        "ix_page_task_assignee_open", "page_task", ["assignee_id", "due_date"], unique=False
    )
    op.create_index("ix_page_task_page_id", "page_task", ["page_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_page_task_page_id", table_name="page_task")
    op.drop_index("ix_page_task_assignee_open", table_name="page_task")
    op.drop_table("page_task")
