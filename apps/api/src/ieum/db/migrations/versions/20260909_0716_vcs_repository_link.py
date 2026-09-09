"""vcs — 연동한 저장소와 이슈에 붙은 커밋·PR (A22, M6)

`vcs_repository.project_ids` 가 JSONB 목록인 이유: 모노레포 하나가 여러
프로젝트의 코드를 담는다. 조인 표를 따로 두면 "이 저장소는 어느 팀의
것인가" 를 읽으려고 표 하나를 더 봐야 하고, 저장소 수는 설치당 수십 개
규모라 그 조인이 값을 하지 않는다.

`vcs_change_link` 의 유니크가 이 마이그레이션의 핵이다. 웹훅은 **다시
온다** — 재전송, 재시도, 사람이 누른 redeliver. 애플리케이션에서만 막으면
두 전송이 동시에 올 때 둘 다 통과해 같은 커밋이 두 줄로 쌓인다.

이슈로 가는 FK 는 `ondelete="CASCADE"` 다. 없으면 이슈를 지운 뒤 링크가
유령으로 남고, 그 줄은 아무 화면에도 안 나오면서 표에만 쌓인다.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "1c9dcbc7116c"
down_revision: str | None = "b5a1fcecc1cf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vcs_repository",
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("project_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
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
            "provider IN ('github', 'gitlab')",
            name=op.f("ck_vcs_repository_vcs_repository_provider"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["user.id"],
            name=op.f("fk_vcs_repository_created_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vcs_repository")),
        sa.UniqueConstraint("provider", "name", name="uq_vcs_repository_provider_name"),
    )
    op.create_table(
        "vcs_change_link",
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("external_ref", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("closing", sa.Boolean(), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=False),
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
            "kind IN ('commit', 'pull_request')",
            name=op.f("ck_vcs_change_link_vcs_change_link_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issue.id"],
            name=op.f("fk_vcs_change_link_issue_id_issue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["repository_id"],
            ["vcs_repository.id"],
            name=op.f("fk_vcs_change_link_repository_id_vcs_repository"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vcs_change_link")),
        sa.UniqueConstraint(
            "issue_id",
            "repository_id",
            "kind",
            "external_ref",
            name="uq_vcs_change_link_target",
        ),
    )
    op.create_index(
        "ix_vcs_change_link_issue",
        "vcs_change_link",
        ["issue_id", "happened_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_vcs_change_link_issue", table_name="vcs_change_link")
    op.drop_table("vcs_change_link")
    op.drop_table("vcs_repository")
