"""notification preference: email digest mode

Revision ID: 7c1f0a2b9d34
Revises: 3a5a7129e911
Create Date: 2026-09-07 01:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "7c1f0a2b9d34"
down_revision: str | None = "3a5a7129e911"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_preference",
        sa.Column("email_mode", sa.String(length=16), nullable=False, server_default="instant"),
    )
    op.add_column(
        "notification_preference",
        sa.Column("last_digest_at", sa.DateTime(timezone=True), nullable=True),
    )
    # 켜 두었던 사람은 그대로 즉시, 꺼 두었던 사람은 그대로 안 받음.
    op.execute("UPDATE notification_preference SET email_mode = 'off' WHERE email IS FALSE")
    op.create_check_constraint(
        "notification_preference_email_mode",
        "notification_preference",
        "email_mode IN ('instant', 'daily', 'off')",
    )
    op.drop_column("notification_preference", "email")


def downgrade() -> None:
    op.add_column(
        "notification_preference",
        sa.Column("email", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute("UPDATE notification_preference SET email = FALSE WHERE email_mode = 'off'")
    op.drop_constraint(
        "notification_preference_email_mode", "notification_preference", type_="check"
    )
    op.drop_column("notification_preference", "last_digest_at")
    op.drop_column("notification_preference", "email_mode")
