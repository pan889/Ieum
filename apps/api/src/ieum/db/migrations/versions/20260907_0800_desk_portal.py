"""desk: 포털·요청 유형·고객 조직 (data-model.md desk 절)

티켓 테이블이 없다. 티켓은 이슈이고(ADR-0003), 데스크 전용 정보만
`ticket_ext` 로 1:1 붙는다.

`customer_membership` 은 data-model.md 에 없던 테이블이다. 소속을 이메일
도메인으로 **읽을 때마다** 계산하면 관리자가 `customer_organization.domains`
를 고치는 순간 누가 어느 티켓을 보는지가 조용히 바뀐다 — 텍스트 필드 하나를
편집해서 ACL 이 움직이는 것은 사고의 모양이다. 그래서 소속을 행으로 굳히고,
`domains` 는 가입 시 기본값을 정하는 힌트로만 쓴다.

`ticket_ext` 의 `guest_email`/`guest_name` 도 추가다. 게스트 요청(C1)은
계정이 없으므로 `reporter_customer_id` 에 넣을 사람이 없다. 검증되지 않은
주소이므로 이슈의 `reporter_id` 에도 올리지 않는다.

Revision ID: 48575e47d426
Revises: f8e1c4a59b36
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "48575e47d426"
down_revision = "f8e1c4a59b36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "customer_organization",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column(
            "domains", postgresql.ARRAY(sa.String(length=253)), server_default="{}", nullable=False
        ),
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
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_organization")),
        sa.UniqueConstraint("name", name=op.f("uq_customer_organization_name")),
    )
    op.create_index(
        "ix_customer_organization_domains",
        "customer_organization",
        ["domains"],
        unique=False,
        postgresql_using="gin",
    )
    op.create_table(
        "customer_membership",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["customer_organization.id"],
            name=op.f("fk_customer_membership_organization_id_customer_organization"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_customer_membership_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_customer_membership")),
    )
    op.create_index(
        "ix_customer_membership_organization_id",
        "customer_membership",
        ["organization_id"],
        unique=False,
    )
    op.create_table(
        "portal",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "theme", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("is_public", sa.Boolean(), nullable=False),
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
            name=op.f("fk_portal_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_portal")),
        sa.UniqueConstraint("slug", name=op.f("uq_portal_slug")),
    )
    op.create_index("ix_portal_project_id", "portal", ["project_id"], unique=False)
    op.create_table(
        "request_type",
        sa.Column("portal_id", sa.Uuid(), nullable=False),
        sa.Column("issue_type_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("icon", sa.String(length=64), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "form_schema",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default='{"fields": []}',
            nullable=False,
        ),
        sa.Column(
            "field_mapping",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
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
            ["issue_type_id"],
            ["issue_type.id"],
            name=op.f("fk_request_type_issue_type_id_issue_type"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["portal_id"],
            ["portal.id"],
            name=op.f("fk_request_type_portal_id_portal"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_type")),
        sa.UniqueConstraint("portal_id", "name", name="uq_request_type_portal_id_name"),
    )
    op.create_index(
        "ix_request_type_issue_type_id", "request_type", ["issue_type_id"], unique=False
    )
    op.create_index("ix_request_type_portal_id", "request_type", ["portal_id"], unique=False)
    op.create_table(
        "ticket_ext",
        sa.Column("issue_id", sa.Uuid(), nullable=False),
        sa.Column("request_type_id", sa.Uuid(), nullable=True),
        sa.Column("reporter_customer_id", sa.Uuid(), nullable=True),
        sa.Column("guest_email", sa.String(length=320), nullable=True),
        sa.Column("guest_name", sa.String(length=200), nullable=True),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column("csat_score", sa.SmallInteger(), nullable=True),
        sa.Column("csat_comment", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "channel IN ('portal', 'email', 'agent')", name=op.f("ck_ticket_ext_ticket_ext_channel")
        ),
        sa.CheckConstraint(
            "reporter_customer_id IS NOT NULL OR guest_email IS NOT NULL OR channel = 'agent'",
            name=op.f("ck_ticket_ext_ticket_ext_has_requester"),
        ),
        sa.CheckConstraint(
            "csat_score IS NULL OR csat_score BETWEEN 1 AND 5",
            name=op.f("ck_ticket_ext_ticket_ext_csat_range"),
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issue.id"],
            name=op.f("fk_ticket_ext_issue_id_issue"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["customer_organization.id"],
            name=op.f("fk_ticket_ext_organization_id_customer_organization"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reporter_customer_id"],
            ["user.id"],
            name=op.f("fk_ticket_ext_reporter_customer_id_user"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["request_type_id"],
            ["request_type.id"],
            name=op.f("fk_ticket_ext_request_type_id_request_type"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("issue_id", name=op.f("pk_ticket_ext")),
    )
    op.create_index("ix_ticket_ext_guest_email", "ticket_ext", ["guest_email"], unique=False)
    op.create_index(
        "ix_ticket_ext_organization_id", "ticket_ext", ["organization_id"], unique=False
    )
    op.create_index(
        "ix_ticket_ext_reporter_customer_id", "ticket_ext", ["reporter_customer_id"], unique=False
    )
    op.create_index(
        "ix_ticket_ext_request_type_id", "ticket_ext", ["request_type_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ticket_ext_request_type_id", table_name="ticket_ext")
    op.drop_index("ix_ticket_ext_reporter_customer_id", table_name="ticket_ext")
    op.drop_index("ix_ticket_ext_organization_id", table_name="ticket_ext")
    op.drop_index("ix_ticket_ext_guest_email", table_name="ticket_ext")
    op.drop_table("ticket_ext")
    op.drop_index("ix_request_type_portal_id", table_name="request_type")
    op.drop_index("ix_request_type_issue_type_id", table_name="request_type")
    op.drop_table("request_type")
    op.drop_index("ix_portal_project_id", table_name="portal")
    op.drop_table("portal")
    op.drop_index("ix_customer_membership_organization_id", table_name="customer_membership")
    op.drop_table("customer_membership")
    op.drop_index(
        "ix_customer_organization_domains",
        table_name="customer_organization",
        postgresql_using="gin",
    )
    op.drop_table("customer_organization")
