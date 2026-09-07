"""identity_provider, user_identity

Revision ID: d6c9a2e37f14
Revises: c5b8f1d20e63
Create Date: 2026-09-07 05:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d6c9a2e37f14"
down_revision: str | None = "c5b8f1d20e63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "identity_provider",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # `server_default` 를 빠뜨리면 INSERT 가 NOT NULL 로 죽는다. 모델은
        # 서버가 채우는 값으로 선언돼 있어 파이썬 쪽에서 안 넣는다.
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
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("issuer", sa.String(length=512), nullable=False),
        sa.Column("client_id", sa.String(length=512), nullable=False),
        sa.Column("client_secret_enc", sa.Text(), nullable=False),
        sa.Column("authorization_endpoint", sa.String(length=512), nullable=False),
        sa.Column("token_endpoint", sa.String(length=512), nullable=False),
        sa.Column("jwks_uri", sa.String(length=512), nullable=False),
        sa.Column("scopes", sa.String(length=512), nullable=False),
        sa.Column("email_claim", sa.String(length=64), nullable=False),
        sa.Column("name_claim", sa.String(length=64), nullable=False),
        sa.Column("groups_claim", sa.String(length=64), nullable=True),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False),
        sa.Column("link_verified_email", sa.Boolean(), nullable=False),
        sa.Column("email_domains", postgresql.JSONB(), nullable=False),
        sa.Column("trust_idp_mfa", sa.Boolean(), nullable=False),
        sa.CheckConstraint("kind IN ('oidc')", name="identity_provider_kind"),
        sa.UniqueConstraint("issuer", "client_id", name="uq_identity_provider_issuer_client"),
    )
    op.create_table(
        "user_identity",
        sa.Column("id", sa.Uuid(), primary_key=True),
        # `server_default` 를 빠뜨리면 INSERT 가 NOT NULL 로 죽는다. 모델은
        # 서버가 채우는 값으로 선언돼 있어 파이썬 쪽에서 안 넣는다.
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
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["identity_provider.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("provider_id", "subject", name="uq_user_identity_provider_subject"),
    )
    op.create_index("ix_user_identity_user_id", "user_identity", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_identity_user_id", table_name="user_identity")
    op.drop_table("user_identity")
    op.drop_table("identity_provider")
