"""큐와 정형 응답 (C3, C10)

큐는 뷰다 — IQL 원문 하나를 저장하고 목록은 볼 때마다 만든다. 티켓을 큐에
넣는 행이 없다.

`visible_role_ids` 는 만들지 않았다(models.py 참조): 큐에서 가려도 그 티켓은
이슈 목록·검색으로 그대로 열리므로, 접근 제어처럼 읽히는데 아무 것도 막지
않는 컬럼이 된다.

Revision ID: 894b991e7319
Revises: 48575e47d426
Create Date: 2026-09-07 14:26:44.403141
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "894b991e7319"
down_revision: str | None = "48575e47d426"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "canned_response",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("shortcut", sa.String(length=40), nullable=True),
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
            name=op.f("fk_canned_response_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_canned_response")),
        sa.UniqueConstraint("project_id", "name", name="uq_canned_response_project_id_name"),
        sa.UniqueConstraint(
            "project_id", "shortcut", name="uq_canned_response_project_id_shortcut"
        ),
    )
    op.create_index(
        "ix_canned_response_project_id", "canned_response", ["project_id"], unique=False
    )
    op.create_table(
        "queue",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("iql", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
            name=op.f("fk_queue_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_queue")),
        sa.UniqueConstraint("project_id", "name", name="uq_queue_project_id_name"),
    )
    op.create_index("ix_queue_project_id", "queue", ["project_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_queue_project_id", table_name="queue")
    op.drop_table("queue")
    op.drop_index("ix_canned_response_project_id", table_name="canned_response")
    op.drop_table("canned_response")
