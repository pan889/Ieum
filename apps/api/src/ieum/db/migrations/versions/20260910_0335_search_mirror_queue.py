"""검색 미러 큐 — OpenSearch 로 아직 못 보낸 색인 키 (ADR-0015)

Revision ID: f4894494245b
Revises: 500b28d8682c
Create Date: 2026-09-10 03:35:03.034137
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4894494245b"
down_revision: str | None = "500b28d8682c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 값이 없는 표다. 키가 PK 라서 같은 문서를 여러 번 고쳐도 줄은 하나 —
    # 미러가 필요한 것은 "지금 상태" 뿐이라 뭉치는 것이 손실이 아니다.
    op.create_table(
        "search_mirror_queue",
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("kind", "entity_id", name=op.f("pk_search_mirror_queue")),
    )
    op.create_index(
        "ix_search_mirror_queue_queued", "search_mirror_queue", ["queued_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_search_mirror_queue_queued", table_name="search_mirror_queue")
    op.drop_table("search_mirror_queue")
