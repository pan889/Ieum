"""page: blog posts

Revision ID: 9e2d41b7c058
Revises: 7c1f0a2b9d34
Create Date: 2026-09-07 02:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9e2d41b7c058"
down_revision: str | None = "7c1f0a2b9d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "page", sa.Column("kind", sa.String(length=16), nullable=False, server_default="page")
    )
    op.add_column("page", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("page_kind", "page", "kind IN ('page', 'blog')")

    # 최상위 slug 유일성에 성격을 넣는다. 없으면 블로그 글 하나가 같은 이름의
    # 최상위 문서를 막는다 — 둘은 주소부터 다른데도.
    op.drop_index("uq_page_space_root_slug", table_name="page")
    op.create_index(
        "uq_page_space_root_slug",
        "page",
        ["space_id", "kind", "slug"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index("ix_page_space_blog", "page", ["space_id", "published_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_page_space_blog", table_name="page")
    op.drop_index("uq_page_space_root_slug", table_name="page")
    op.create_index(
        "uq_page_space_root_slug",
        "page",
        ["space_id", "slug"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.drop_constraint("page_kind", "page", type_="check")
    op.drop_column("page", "published_at")
    op.drop_column("page", "kind")
