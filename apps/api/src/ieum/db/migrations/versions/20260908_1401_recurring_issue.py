"""recurring_issue — 주기적으로 이슈를 만드는 스케줄 (A27, M5)

이슈의 사본이 아니라 **틀**이다. 만들어진 이슈를 고쳐도 다음 이슈는 틀에서
나오고, 틀을 고쳐도 이미 만들어진 이슈는 그대로다.

`next_run_at` 을 값으로 들고 있는다. 크론 문자열을 저장하고 매번 계산하지
않는 이유: 계산이 틀리면 **아무 일도 일어나지 않고**, 아무 일도 일어나지 않는
것은 화면에 안 보인다. 값이면 화면이 "다음: 9월 15일 09:00" 을 보여 줄 수
있고, 지나갔는데 안 돌았다는 것도 보인다.

부분 색인(`is_enabled` 인 것만)을 두는 이유: 워커가 15초마다 "지금 지난 것" 을
훑는다. 꺼진 스케줄은 그 질의에 애초에 안 들어와야 한다.

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b5a1fcecc1cf"
down_revision: str | None = "59c965f75275"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recurring_issue",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=500), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("type_id", sa.Uuid(), nullable=True),
        sa.Column("assignee_id", sa.Uuid(), nullable=True),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column(
            "labels", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False
        ),
        sa.Column("due_in_days", sa.Integer(), nullable=True),
        sa.Column("cadence", sa.String(length=20), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("minute", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=True),
        sa.Column("day", sa.Integer(), nullable=True),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_issue_id", sa.Uuid(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            name=op.f("fk_recurring_issue_assignee_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_recurring_issue_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["last_issue_id"],
            ["issue.id"],
            name=op.f("fk_recurring_issue_last_issue_id_issue"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_recurring_issue_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["issue_type.id"],
            name=op.f("fk_recurring_issue_type_id_issue_type"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recurring_issue")),
        sa.UniqueConstraint("project_id", "name", name="uq_recurring_issue_project_id_name"),
    )
    op.create_index(
        "ix_recurring_issue_due",
        "recurring_issue",
        ["next_run_at"],
        unique=False,
        postgresql_where=sa.text("is_enabled"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recurring_issue_due",
        table_name="recurring_issue",
        postgresql_where=sa.text("is_enabled"),
    )
    op.drop_table("recurring_issue")
