"""데스크의 이름 유일성을 **살아 있는 행에만** 건다

편도로 보관되는 여섯 가지가 이름을 한 번 쓰고 버리고 있었다. 큐를 지우면
`archived_at` 만 찍히고 목록에서 사라지는데, 유니크 제약은 보관된 행까지
세므로 같은 이름을 다시 만들 수 없다. 화면은 "같은 이름의 큐가 있다" 고
말하고, 사용자는 그 큐를 어디서도 볼 수 없다. 보관을 푸는 길도 없다.

보관하는 판단 자체는 맞다 — 감사 로그와 링크가 그 이름을 들고 있다. 틀린
것은 **유일성의 범위**다. "한 프로젝트에 같은 이름의 큐가 둘 있으면 안
된다" 는 규칙이 말하는 큐는 살아 있는 큐다. 그래서 제약을
`WHERE archived_at IS NULL` 부분 유니크 인덱스로 바꾼다.

되돌릴 수 있는 보관(포털·요청 유형·고객 조직·자산 종류)은 건드리지 않는다.
거기는 이름을 되찾는 길이 이미 있다 — 보관을 풀면 된다.

이슈 번호(`uq_issue_project_id_key_seq`)와 프로젝트 키도 건드리지 않는다.
그쪽은 **영구 참조**라서 재사용되면 안 된다. `WEB-9` 가 두 번 있으면 지난
링크가 어디를 가리키는지 알 수 없다.

## 자동화 규칙은 반대 방향으로 깨져 있었다

`AutomationRule` 의 가드는 이미 `archived_at IS NULL` 을 보고 있었는데 DB
제약은 안 그랬다. 그래서 규칙을 지우고 같은 이름으로 다시 만들면 가드는
통과하고 저장이 IntegrityError 로 **500** 이 났다. 같은 변경이 이것도 맞춘다.

## 되돌리기

`downgrade()` 는 보관된 행 중에 이름이 겹치는 것이 있으면 실패한다 — 이
판에서 같은 이름을 다시 쓴 뒤 보관했다면 그렇게 된다. 막을 방법이 없다:
옛 제약이 요구하는 것을 이 판의 데이터가 이미 어겼기 때문이다. 억지로
지우면 사용자가 만든 것을 없애는 일이라 하지 않는다. 되돌려야 하면 겹치는
쪽의 이름을 손으로 바꾸고 다시 시도한다.

Revision ID: 8e4f1c07ab52
Revises: 6b1c0a9f2d34
Create Date: 2026-09-16 03:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8e4f1c07ab52"
down_revision: str | None = "6b1c0a9f2d34"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIVE = sa.text("archived_at IS NULL")

#: (테이블, 옛 유니크 제약 이름, 새 부분 인덱스 이름, 컬럼들)
TARGETS: tuple[tuple[str, str, str, list[str]], ...] = (
    ("queue", "uq_queue_project_id_name", "uq_queue_live_name", ["project_id", "name"]),
    (
        "canned_response",
        "uq_canned_response_project_id_name",
        "uq_canned_response_live_name",
        ["project_id", "name"],
    ),
    (
        "canned_response",
        "uq_canned_response_project_id_shortcut",
        "uq_canned_response_live_shortcut",
        ["project_id", "shortcut"],
    ),
    (
        "business_calendar",
        "uq_business_calendar_name",
        "uq_business_calendar_live_name",
        ["name"],
    ),
    (
        "sla_policy",
        "uq_sla_policy_project_id_name",
        "uq_sla_policy_live_name",
        ["project_id", "name"],
    ),
    (
        "automation_rule",
        "uq_automation_rule_project_id_name",
        "uq_automation_rule_live_name",
        ["project_id", "name"],
    ),
    (
        "email_channel",
        "uq_email_channel_address",
        "uq_email_channel_live_address",
        ["address"],
    ),
)


def upgrade() -> None:
    for table, old_constraint, new_index, columns in TARGETS:
        op.drop_constraint(old_constraint, table, type_="unique")
        op.create_index(new_index, table, columns, unique=True, postgresql_where=LIVE)


def downgrade() -> None:
    for table, old_constraint, new_index, columns in TARGETS:
        op.drop_index(new_index, table_name=table, postgresql_where=LIVE)
        # 보관된 행끼리 이름이 겹치면 여기서 실패한다. 윗글 "되돌리기" 참고.
        op.create_unique_constraint(old_constraint, table, columns)
