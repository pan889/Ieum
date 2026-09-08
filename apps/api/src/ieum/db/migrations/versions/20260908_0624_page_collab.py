"""page_collab — 동시 편집의 공유 초안 (B16, M5)

`page_draft` 와 나란히 서는 표다. 초안은 사람마다 하나이고 이건 **문서마다
하나**다 — 같이 편집하는 자리에서는 "내 초안" 이라는 것이 없다.

`state` 는 CRDT 문서 전체 상태다. 마크다운을 함께 담지 않는다: 텍스트는
상태에서 뽑을 수 있지만 거꾸로는 못 하고, 두 벌을 두면 어긋난다.

기존 페이지에는 아무 것도 넣지 않는다. 방은 처음 붙는 사람이 만들고, 행이
없으면 지금 게시된 판의 본문에서 시작한다 — 그래서 마이그레이션이 데이터를
만들 이유가 없다.

Revision ID: 152dd79bde6a
Revises: 308fb6926d4a
Create Date: 2026-09-08 06:24:44.505990
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "152dd79bde6a"
down_revision: str | None = "308fb6926d4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "page_collab",
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.LargeBinary(), nullable=False),
        sa.Column("saved_by", sa.Uuid(), nullable=True),
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
            ["page_id"], ["page.id"], name=op.f("fk_page_collab_page_id_page"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["saved_by"],
            ["user.id"],
            name=op.f("fk_page_collab_saved_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_page_collab")),
        sa.UniqueConstraint("page_id", name=op.f("uq_page_collab_page_id")),
    )


def downgrade() -> None:
    op.drop_table("page_collab")
