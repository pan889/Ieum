"""user_group.idp_provider_id · user.invite_generation

## IdP 그룹에 주인을 적는다

SSO 로그인이 클레임의 그룹 이름으로 그룹을 찾을 때 **이름과 `source='idp'`
만** 봤다. IdP 가 둘이면(협력사·자회사) 한쪽이 `admins` 를 보내는 순간 다른
쪽이 만든 `admins` 에 들어가 그 그룹에 붙은 역할을 그대로 받았다. 그래서
그룹을 만든 IdP 를 적는다.

**이미 있는 `idp` 그룹은 가진 증거로만 채운다.**

1. IdP 가 하나뿐이면 그 IdP 가 만든 것이다.
2. 둘 이상이면, 그룹 멤버들이 이어진 IdP(`user_identity`)가 **정확히 하나**일
   때만 그 IdP 로 본다. 클레임 그룹의 멤버는 그 그룹 이름을 보낸 IdP 의
   로그인으로만 들어오므로, 멤버 전원이 한 IdP 에만 이어져 있으면 그 IdP 가
   보낸 것이다.

나머지(멤버가 없거나 여러 IdP 에 걸친 그룹)는 `NULL` 로 남는다. 그런 그룹에는
어떤 IdP 도 사람을 **새로 넣지 않는다** — 이미 든 사람은 자기 IdP 가 그 이름을
계속 보내는 동안 남고, 안 보내면 빠진다. 다시 쓰려면 관리자가 그 그룹을 지우고
(역할 할당도 같이 지워진다) 다음 로그인이 주인을 적어 새로 만들게 한다.

## 초대장에 세대를 붙인다

초대 토큰은 DB 행 없이 봉해서만 준다. 다시 초대해 계정이 `invited` 로
돌아가면 7일 안에 나갔던 옛 초대 메일이 상태 검사를 다시 통과했다. 다시 초대할
때마다 세대를 올리고, 토큰이 실어 온 세대가 다르면 거절한다. 기존 행은 0 이다 —
이 판 이전에 나간 토큰도 0 으로 읽으므로 아직 수락 안 한 초대는 그대로 통한다.

Revision ID: 3dfcf757dd4a
Revises: 8e4f1c07ab52
Create Date: 2026-10-20 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "3dfcf757dd4a"
down_revision: str | None = "8e4f1c07ab52"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user_group", sa.Column("idp_provider_id", sa.Uuid(), nullable=True))
    op.create_index(
        "ix_user_group_idp_provider_id", "user_group", ["idp_provider_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_user_group_idp_provider_id_identity_provider"),
        "user_group",
        "identity_provider",
        ["idp_provider_id"],
        ["id"],
        ondelete="SET NULL",
    )
    # 1. IdP 가 하나뿐인 설치본.
    op.execute(
        sa.text(
            """
            UPDATE user_group
               SET idp_provider_id = (SELECT id FROM identity_provider)
             WHERE source = 'idp'
               AND idp_provider_id IS NULL
               AND (SELECT count(*) FROM identity_provider) = 1
            """
        )
    )
    # 2. 여럿인 설치본 — 멤버 전원이 한 IdP 에만 이어진 그룹.
    op.execute(
        sa.text(
            """
            UPDATE user_group AS g
               SET idp_provider_id = sole.provider_id
              FROM (
                    SELECT gm.group_id,
                           (array_agg(DISTINCT ui.provider_id))[1] AS provider_id
                      FROM group_member AS gm
                      JOIN user_identity AS ui ON ui.user_id = gm.user_id
                     GROUP BY gm.group_id
                    HAVING count(DISTINCT ui.provider_id) = 1
                   ) AS sole
             WHERE g.id = sole.group_id
               AND g.source = 'idp'
               AND g.idp_provider_id IS NULL
            """
        )
    )

    op.add_column(
        "user",
        sa.Column("invite_generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user", "invite_generation")
    op.drop_constraint(
        op.f("fk_user_group_idp_provider_id_identity_provider"), "user_group", type_="foreignkey"
    )
    op.drop_index("ix_user_group_idp_provider_id", table_name="user_group")
    op.drop_column("user_group", "idp_provider_id")
