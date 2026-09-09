"""approval — 요청 유형의 승인 단계와 그 결정 (C12, M6)

`request_type.approval` 이 규칙이고(누가, 몇 명이), `approval` 이 요청 하나,
`approval_vote` 가 사람 한 명의 결정이다.

**부분 유니크가 이 마이그레이션의 핵이다.** 기다리는 승인은 티켓당 하나여야
한다: 애플리케이션에서만 막으면 요청이 두 번 들어올 때 둘 다 통과하고, 그러면
한쪽만 승인된 채로 문이 열린다. 끝난 승인은 여러 개 쌓이므로(거절 뒤 다시
요청하는 흐름) 전체 유니크가 아니라 `status = 'pending'` 부분 유니크다.

`approval_vote` 의 `(approval_id, user_id)` 유니크는 **한 사람이 한 번만
결정한다**는 규칙이다. 두 탭에서 두 번 누르는 것을 DB 가 막는다.

`approver_ids` 는 찍어 둔 명단이다. 그룹을 그때그때 펼치지 않는 이유는
`modules/desk/approvals.py` 머리에 적어 뒀다 — 그룹이 바뀌면 "전원 동의" 의
셈과 "누구를 기다렸나" 가 함께 사라진다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "6218a4ab6e7f"
down_revision: str | None = "1c9dcbc7116c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("request_type_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column(
            "approver_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="[]",
            nullable=False,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by", sa.Uuid(), nullable=True),
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
        sa.CheckConstraint("mode IN ('one', 'all')", name=op.f("ck_approval_approval_mode")),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'declined', 'cancelled')",
            name=op.f("ck_approval_approval_status"),
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by"],
            ["user.id"],
            name=op.f("fk_approval_cancelled_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"], ["issue.id"], name=op.f("fk_approval_issue_id_issue"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["request_type_id"],
            ["request_type.id"],
            name=op.f("fk_approval_request_type_id_request_type"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval")),
    )
    op.create_index("ix_approval_issue_id", "approval", ["issue_id"], unique=False)
    op.create_index(
        "uq_approval_pending_issue",
        "approval",
        ["issue_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "approval_vote",
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=8), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
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
        sa.CheckConstraint(
            "decision IN ('approve', 'decline')",
            name=op.f("ck_approval_vote_approval_vote_decision"),
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval.id"],
            name=op.f("fk_approval_vote_approval_id_approval"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["user.id"], name=op.f("fk_approval_vote_user_id_user"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_vote")),
        sa.UniqueConstraint("approval_id", "user_id", name="uq_approval_vote_person"),
    )
    op.add_column(
        "request_type",
        sa.Column("approval", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("request_type", "approval")
    op.drop_table("approval_vote")
    op.drop_index(
        "uq_approval_pending_issue",
        table_name="approval",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index("ix_approval_issue_id", table_name="approval")
    op.drop_table("approval")
