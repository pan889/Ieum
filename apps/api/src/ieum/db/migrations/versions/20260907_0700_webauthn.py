"""WebAuthn/패스키 (auth.md 3절)

`mfa_credential` 이 종류마다 다른 것을 든다. TOTP·백업 코드는 **비밀**을,
WebAuthn 은 **공개키**를 든다 — 공개키를 `secret_enc` 에 밀어 넣으면 이름이
거짓이 되고, 다음 사람이 그 칼럼을 비밀처럼 다루거나 그 반대가 된다.

Revision ID: f8e1c4a59b36
Revises: e7d0b3f48a25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f8e1c4a59b36"
down_revision = "e7d0b3f48a25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("mfa_credential", "secret_enc", nullable=True)

    op.add_column(
        "mfa_credential",
        sa.Column("webauthn_credential_id", sa.String(length=512), nullable=True),
    )
    op.create_unique_constraint(
        "uq_mfa_credential_webauthn_credential_id",
        "mfa_credential",
        ["webauthn_credential_id"],
    )
    op.add_column("mfa_credential", sa.Column("webauthn_public_key", sa.Text(), nullable=True))
    op.add_column(
        "mfa_credential",
        sa.Column("webauthn_sign_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "mfa_credential",
        sa.Column(
            "webauthn_transports",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "mfa_credential",
        sa.Column("webauthn_backed_up", sa.Boolean(), nullable=False, server_default=sa.false()),
    )

    op.create_check_constraint(
        "mfa_credential_secret_required",
        "mfa_credential",
        "(kind = 'webauthn') OR (secret_enc IS NOT NULL)",
    )
    op.create_check_constraint(
        "mfa_credential_webauthn_required",
        "mfa_credential",
        "(kind <> 'webauthn') OR ("
        "webauthn_credential_id IS NOT NULL AND webauthn_public_key IS NOT NULL)",
    )

    # 챌린지는 **세션에** 적는다. 세션 밖에 두면 한 창에서 받은 챌린지를
    # 다른 창이 소진할 수 있고, "이 브라우저가 지금 키를 만졌다" 가 무너진다.
    op.add_column("session", sa.Column("webauthn_challenge", sa.String(length=255), nullable=True))
    op.add_column(
        "session",
        sa.Column("webauthn_challenge_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("session", "webauthn_challenge_expires_at")
    op.drop_column("session", "webauthn_challenge")

    op.drop_constraint("mfa_credential_webauthn_required", "mfa_credential", type_="check")
    op.drop_constraint("mfa_credential_secret_required", "mfa_credential", type_="check")

    for column in (
        "webauthn_backed_up",
        "webauthn_transports",
        "webauthn_sign_count",
        "webauthn_public_key",
    ):
        op.drop_column("mfa_credential", column)
    op.drop_constraint("uq_mfa_credential_webauthn_credential_id", "mfa_credential", type_="unique")
    op.drop_column("mfa_credential", "webauthn_credential_id")

    # 되돌리려면 WebAuthn 행이 없어야 한다. 있으면 NOT NULL 이 실패하고,
    # 그게 맞다 — 조용히 지우는 것보다 낫다.
    op.alter_column("mfa_credential", "secret_enc", nullable=False)
