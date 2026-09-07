"""메일 채널: 창구와 오간 메일의 기록, 그리고 반송 표시 (C6)

`email_channel.inbound_password_enc` 를 JSONB 밖으로 뽑았다. data-model.md 는
설정 한 칸(`inbound`)을 적어 두었지만 그 안에 비밀번호가 들어가면 설정을
되돌려주는 API·로그·오류 보고에 그대로 실린다 — 문서에 따로 반영한다.

Revision ID: 717660dd9b17
Revises: bbf2793781b0
Create Date: 2026-09-07 17:48:43.997884
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "717660dd9b17"
down_revision: str | None = "bbf2793781b0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "email_channel",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("outbound_from", sa.String(length=320), nullable=False),
        sa.Column(
            "inbound", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column("inbound_password_enc", sa.Text(), nullable=True),
        sa.Column("default_request_type_id", sa.Uuid(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=True),
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
            ["default_request_type_id"],
            ["request_type.id"],
            name=op.f("fk_email_channel_default_request_type_id_request_type"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_email_channel_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_channel")),
        sa.UniqueConstraint("address", name=op.f("uq_email_channel_address")),
    )
    op.create_index("ix_email_channel_project_id", "email_channel", ["project_id"], unique=False)
    op.create_index(
        "ix_email_channel_request_type_id",
        "email_channel",
        ["default_request_type_id"],
        unique=False,
    )
    op.create_table(
        "email_message",
        sa.Column("issue_id", sa.Uuid(), nullable=True),
        sa.Column("channel_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.String(length=998), nullable=False),
        sa.Column("in_reply_to", sa.String(length=998), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=998), nullable=False),
        sa.Column("raw_key", sa.String(length=512), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("skipped_reason", sa.String(length=64), nullable=True),
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
            "direction IN ('inbound', 'outbound')",
            name=op.f("ck_email_message_email_message_direction"),
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"],
            ["email_channel.id"],
            name=op.f("fk_email_message_channel_id_email_channel"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["issue_id"],
            ["issue.id"],
            name=op.f("fk_email_message_issue_id_issue"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_message")),
        sa.UniqueConstraint("message_id", name=op.f("uq_email_message_message_id")),
    )
    op.create_index("ix_email_message_in_reply_to", "email_message", ["in_reply_to"], unique=False)
    op.create_index("ix_email_message_issue_id", "email_message", ["issue_id"], unique=False)
    op.add_column(
        "ticket_ext", sa.Column("email_bounced_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("ticket_ext", "email_bounced_at")
    op.drop_index("ix_email_message_issue_id", table_name="email_message")
    op.drop_index("ix_email_message_in_reply_to", table_name="email_message")
    op.drop_table("email_message")
    op.drop_index("ix_email_channel_request_type_id", table_name="email_channel")
    op.drop_index("ix_email_channel_project_id", table_name="email_channel")
    op.drop_table("email_channel")
