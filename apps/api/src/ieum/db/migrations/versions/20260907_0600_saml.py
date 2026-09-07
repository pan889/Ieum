"""SAML 2.0 IdP 설정과 흐름 (auth.md 4절)

OIDC 전용 칼럼을 nullable 로 내리고, 종류별로 무엇이 있어야 하는지를 CHECK 로
못 박는다. 반쯤 채운 IdP 행은 등록할 때가 아니라 **누가 로그인하려는 순간**
드러나기 때문이다.

Revision ID: e7d0b3f48a25
Revises: d6c9a2e37f14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e7d0b3f48a25"
down_revision = "d6c9a2e37f14"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # OIDC 전용. SAML 행은 이 넷을 쓰지 않는다.
    for column in ("client_id", "client_secret_enc", "token_endpoint", "jwks_uri"):
        op.alter_column("identity_provider", column, nullable=True)

    op.add_column(
        "identity_provider",
        sa.Column(
            "saml_certificates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("identity_provider", sa.Column("saml_sp_key_enc", sa.Text(), nullable=True))
    op.add_column("identity_provider", sa.Column("saml_sp_certificate", sa.Text(), nullable=True))
    op.add_column(
        "identity_provider",
        sa.Column("saml_want_encrypted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "identity_provider",
        sa.Column(
            "saml_allow_idp_initiated",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.drop_constraint("identity_provider_kind", "identity_provider", type_="check")
    op.create_check_constraint(
        "identity_provider_kind", "identity_provider", "kind IN ('oidc', 'saml')"
    )
    op.create_check_constraint(
        "identity_provider_oidc_fields",
        "identity_provider",
        "(kind <> 'oidc') OR ("
        "client_id IS NOT NULL AND client_secret_enc IS NOT NULL"
        " AND token_endpoint IS NOT NULL AND jwks_uri IS NOT NULL)",
    )
    op.create_check_constraint(
        "identity_provider_saml_fields",
        "identity_provider",
        "(kind <> 'saml') OR jsonb_array_length(saml_certificates) > 0",
    )
    # SAML 행은 client_id 가 비어 있어 (issuer, client_id) 유일 제약이 걸리지
    # 않는다 — Postgres 는 NULL 을 서로 다르게 본다.
    op.create_index(
        "uq_identity_provider_saml_issuer",
        "identity_provider",
        ["issuer"],
        unique=True,
        postgresql_where=sa.text("kind = 'saml'"),
    )

    op.create_table(
        "saml_flow",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("handoff_hash", sa.String(length=64), nullable=True),
        sa.Column("idp_verified_mfa", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["provider_id"], ["identity_provider.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("request_id"),
        sa.UniqueConstraint("handoff_hash"),
    )
    op.create_index("ix_saml_flow_expires_at", "saml_flow", ["expires_at"])

    op.create_table(
        "saml_seen_assertion",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("provider_id", sa.Uuid(), nullable=False),
        sa.Column("assertion_id", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["provider_id"], ["identity_provider.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("assertion_id"),
    )
    op.create_index("ix_saml_seen_assertion_expires_at", "saml_seen_assertion", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_saml_seen_assertion_expires_at", table_name="saml_seen_assertion")
    op.drop_table("saml_seen_assertion")
    op.drop_index("ix_saml_flow_expires_at", table_name="saml_flow")
    op.drop_table("saml_flow")

    op.drop_index("uq_identity_provider_saml_issuer", table_name="identity_provider")
    op.drop_constraint("identity_provider_saml_fields", "identity_provider", type_="check")
    op.drop_constraint("identity_provider_oidc_fields", "identity_provider", type_="check")
    op.drop_constraint("identity_provider_kind", "identity_provider", type_="check")
    op.create_check_constraint("identity_provider_kind", "identity_provider", "kind IN ('oidc')")

    for column in (
        "saml_allow_idp_initiated",
        "saml_want_encrypted",
        "saml_sp_certificate",
        "saml_sp_key_enc",
        "saml_certificates",
    ):
        op.drop_column("identity_provider", column)

    # 되돌리려면 SAML 행이 없어야 한다. 있으면 NOT NULL 이 실패하고, 그게 맞다 —
    # 조용히 지우는 것보다 낫다.
    for column in ("jwks_uri", "token_endpoint", "client_secret_enc", "client_id"):
        op.alter_column("identity_provider", column, nullable=False)
