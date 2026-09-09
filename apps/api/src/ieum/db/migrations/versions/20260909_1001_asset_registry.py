"""asset — 자산·구성 항목과 티켓의 연결 (C15, M6)

`asset_type` 이 종류(설치가 짓는 이름), `asset` 이 항목 하나, `asset_link` 가
"이 티켓은 이 자산에 대한 것" 이다. 로드맵이 약속한 것은 그 연결이다.

**속성을 자유롭게 담는 열이 없다.** JSONB 하나를 두고 아무 키나 넣게 하면 두
사람이 같은 것을 다르게 적고(`serial` / `sn` / `시리얼`), 화면은 그것을 그릴
수 없다. 정의가 필요해지면 정의를 갖춘 채로 더한다.

`asset.tag`(자산번호)는 **있으면 유일하다.** 없는 자산이 흔하므로(교실,
서비스) NULL 을 허용하고, Postgres 의 유니크는 NULL 을 여럿 받아들인다.
저장할 때 대문자로 맞춘다 — 사람이 손으로 치는 값이라 `A-1024` 와 `a-1024`
가 두 자산이 되면 재고를 못 믿는다.

`asset_type` 으로 가는 FK 는 **RESTRICT** 다. 종류를 지우면 그 종류의 자산이
가리킬 곳을 잃으므로, 지우는 대신 접는다(`archived_at`).

`asset_link` 는 `entity_link`(org 의 중립 링크)를 쓰지 않는다. 그쪽은 UUID 에
타입이 없어서 자산을 지우면 링크가 유령으로 남는다 — `desk` 는 이미
`issues.contracts` 를 부르므로 FK 를 걸 수 있다.

이름 색인(`ix_asset_name`)이 있는 이유: 자산은 수천 개가 되고 화면은
**검색으로만** 고르게 한다 (ux-principles "잘린 선택 목록").
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7de774e3a762"
down_revision: str | None = "6218a4ab6e7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "asset_type",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("icon", sa.String(length=64), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset_type")),
        sa.UniqueConstraint("name", name=op.f("uq_asset_type_name")),
    )
    op.create_table(
        "asset",
        sa.Column("type_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("tag", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
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
            "status IN ('in_use', 'spare', 'repair', 'retired')", name=op.f("ck_asset_asset_status")
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["customer_organization.id"],
            name=op.f("fk_asset_organization_id_customer_organization"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["user.id"], name=op.f("fk_asset_owner_id_user"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["type_id"],
            ["asset_type.id"],
            name=op.f("fk_asset_type_id_asset_type"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_asset")),
        sa.UniqueConstraint("tag", name=op.f("uq_asset_tag")),
    )
    op.create_index("ix_asset_name", "asset", ["name"], unique=False)
    op.create_index("ix_asset_organization_id", "asset", ["organization_id"], unique=False)
    op.create_index("ix_asset_type_id", "asset", ["type_id"], unique=False)
    op.create_table(
        "asset_link",
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column(
            "linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("linked_by", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["asset.id"],
            name=op.f("fk_asset_link_asset_id_asset"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issue.id"],
            name=op.f("fk_asset_link_issue_id_issue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["linked_by"],
            ["user.id"],
            name=op.f("fk_asset_link_linked_by_user"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("asset_id", "issue_id", name=op.f("pk_asset_link")),
    )
    op.create_index("ix_asset_link_issue", "asset_link", ["issue_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_asset_link_issue", table_name="asset_link")
    op.drop_table("asset_link")
    op.drop_index("ix_asset_type_id", table_name="asset")
    op.drop_index("ix_asset_organization_id", table_name="asset")
    op.drop_index("ix_asset_name", table_name="asset")
    op.drop_table("asset")
    op.drop_table("asset_type")
