"""user.pre_suspend_status — 되살릴 때 어디로 돌아갈지 기억한다

정지를 풀 때 어떤 상태로 갈지를 `password_hash` 유무로 갈랐다. 비밀번호가
없는 계정은 두 가지인데 — 초대만 받고 수락하지 않은 사람과, IdP 로 들어오는
SSO·SCIM 계정 — 한 열로는 구분이 안 된다. 그래서 Okta 로 로그인하던 사람을
휴직 동안 정지했다 되살리면 `invited` 가 됐고, SSO 버튼은 401 을 돌려줬다.
관리자가 다시 눌러도 이미 정지 상태가 아니라 아무 일도 안 났고, 다시
초대하려 하면 이메일이 이미 있다고 409 였다. DB 를 손으로 고치기 전까지
그 계정은 살아나지 않았다.

기억해 두면 갈릴 것이 없다.

**이미 정지된 행은 지금 채워 넣는다.** 이 판이 올라간 뒤 정지된 것만 열이
차면, 그 전에 정지된 계정은 여전히 옛 규칙을 탄다. 가진 증거로 채운다 —
비밀번호가 있거나 한 번이라도 로그인한 적이 있으면 `active` 였다고 본다.
남는 것(비밀번호도 없고 로그인한 적도 없는 계정)은 `invited` 로 본다.

Revision ID: 6b1c0a9f2d34
Revises: 2707d238a528
Create Date: 2026-09-12 12:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6b1c0a9f2d34"
down_revision: str | None = "2707d238a528"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("pre_suspend_status", sa.String(length=16), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE "user"
               SET pre_suspend_status = CASE
                     WHEN password_hash IS NOT NULL OR last_login_at IS NOT NULL
                       THEN 'active'
                     ELSE 'invited'
                   END
             WHERE status = 'suspended'
            """
        )
    )


def downgrade() -> None:
    op.drop_column("user", "pre_suspend_status")
