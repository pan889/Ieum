"""보안 레벨을 목록 질의에 거는 필터.

`SecurityLevelGuard`(service.py) 는 **단건 조회에서만** 돈다. core 의 권한
서비스가 객체 수준 관문을 부르는 조건이 `subject=` 를 받았을 때이기 때문이다.
`GET /issues/{id}` 는 그 이슈를 넘기므로 걸리지만, 목록·IQL 검색·저장 필터·
CSV 내보내기·보드·간트·캘린더·리포트·데스크 큐는 subject 가 없다 — 스코프만
맞으면 제한 이슈가 그대로 실려 나갔다. 단건은 403 인데 목록에는 요약이 보이는,
서로 반대로 말하는 상태였다.

**같은 규칙을 SQL 로 한 번 더 쓴다.** 관문을 목록에서도 부르려면 행마다 한
번씩 DB 를 다녀와야 하고, 그건 페이지 하나에 수백 번이다. 조건으로 내려보내면
Postgres 가 인덱스와 함께 한 번에 판단한다.

판단 기준은 관문과 글자 그대로 같다 — `grantees` 중 `kind` 가 `user` 나
`group` 인 것의 `id` 가 액터의 주체 집합과 겹치면 보인다. 한쪽만 고치면 다시
어긋나므로 둘은 같이 움직여야 한다.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ColumnElement, exists, func, literal, select, true

from ieum.modules.issues.models import Issue, SecurityLevel

#: 관문(`SecurityLevelGuard.allows`)이 보는 것과 같은 종류. `role` 은 양쪽 다
#: 보지 않는다 — 역할 기반 허용은 아직 없다.
GRANTEE_KINDS = ("user", "group")


def visible_issues(principal_ids: frozenset[UUID]) -> ColumnElement[bool]:
    """이 주체들이 볼 수 있는 이슈만 남기는 조건.

    보안 레벨이 없는 이슈는 그대로 통과한다. 레벨 행이 지워진 경우는
    `security_level_id` 가 `SET NULL` 로 비므로 여기서도 통과한다 —
    아무도 못 보는 이슈가 영구히 남는 쪽이 더 나쁘다는 관문의 판단과 같다.
    """
    grantee = func.jsonb_array_elements(SecurityLevel.grantees).table_valued("value").alias("g")
    item = grantee.c.value
    granted = exists(
        select(literal(1))
        .select_from(SecurityLevel)
        .join(grantee, true())
        .where(
            SecurityLevel.id == Issue.security_level_id,
            item.op("->>")(literal("kind")).in_(GRANTEE_KINDS),
            item.op("->>")(literal("id")).in_([str(p) for p in principal_ids]),
        )
    )
    return Issue.security_level_id.is_(None) | granted


__all__ = ["GRANTEE_KINDS", "visible_issues"]
