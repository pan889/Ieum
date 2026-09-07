"""요청 유형에 지식베이스 스페이스를 건다 (C8)

`kind = "kb"` 인 스페이스만 걸 수 있다는 것은 서비스가 강제한다 — DB 의
CHECK 로 두면 스페이스의 종류를 바꿀 때 그 제약이 뒤늦게 터진다.

Revision ID: b99e8063aa70
Revises: 717660dd9b17
Create Date: 2026-09-07 18:48:11.920508
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b99e8063aa70"
down_revision: str | None = "717660dd9b17"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("request_type", sa.Column("kb_space_id", sa.Uuid(), nullable=True))
    op.create_index("ix_request_type_kb_space_id", "request_type", ["kb_space_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_request_type_kb_space_id_space"),
        "request_type",
        "space",
        ["kb_space_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_request_type_kb_space_id_space"), "request_type", type_="foreignkey"
    )
    op.drop_index("ix_request_type_kb_space_id", table_name="request_type")
    op.drop_column("request_type", "kb_space_id")
