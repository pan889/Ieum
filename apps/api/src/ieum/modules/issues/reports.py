"""리포트 — IQL 이 고른 것을 센다 (A29, M5).

## 합이 총계와 맞아야 한다

리포트의 유일한 약속이다. 막대 다섯 개를 더했는데 "전체 40건" 과 안 맞으면
사람은 어느 쪽을 믿어야 할지 모르고, 대개 **큰 쪽을 믿는다.**

그래서 세는 길은 검색과 **같은 조건**을 쓰고(`SearchService` 와 같은 컴파일러,
같은 ACL), 빈 값을 버리지 않으며, 여럿에 걸리는 기준은 그렇다고 적는다.

## 담당자 없음은 빈 칸이 아니라 하나의 칸이다

`GROUP BY assignee_id` 는 `NULL` 을 한 무리로 묶어 준다. 그걸 화면에서 빼면
"담당자 없는 일" 이 리포트에서 사라지는데, 그건 보통 **가장 봐야 할 무리**다.

## 라벨은 합이 총계를 넘는다

이슈 하나가 라벨 셋을 달면 세 칸에 들어간다. 그건 사실이므로 숨기지 않고
`multi_valued` 로 적는다 — 안 적으면 합이 총계와 안 맞는 것이 버그로 보인다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import Select, SQLColumnExpression, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ValidationError
from ieum.core.permissions import PermissionService
from ieum.modules.issues import permissions as perms
from ieum.modules.issues.iql.compiler import GROUPED_LABEL, compile_query, project_keys_in
from ieum.modules.issues.iql.parser import parse
from ieum.modules.issues.iql.registry import FunctionContext
from ieum.modules.issues.models import (
    Issue,
    IssueType,
    Sprint,
    WorkflowState,
)
from ieum.modules.org import contracts as org

#: 칸이 이보다 많으면 그림이 못 읽힌다. 넘으면 자르고 **잘랐다고 말한다** —
#: 잘린 리포트를 다 그린 리포트로 읽으면 합이 안 맞는 이유를 모른다.
MAX_BUCKETS = 50


@dataclass(frozen=True, slots=True)
class GroupSpec:
    """셀 수 있는 기준 하나."""

    #: 값이 여럿일 수 있나. 라벨이 그렇다 — 합이 총계를 넘는다.
    multi_valued: bool = False
    #: 값이 없을 수 있나. 그 무리도 하나의 칸이다.
    nullable: bool = True


#: 셀 수 있는 기준.
#:
#: **일부러 좁다.** 아무 필드로나 묶게 하면 `summary` 로 묶어 칸이 이슈 수
#: 만큼 나오는 리포트가 생긴다 — 그건 리포트가 아니라 목록이다.
GROUPS: dict[str, GroupSpec] = {
    "status": GroupSpec(nullable=False),
    "statuscategory": GroupSpec(nullable=False),
    "assignee": GroupSpec(),
    "reporter": GroupSpec(),
    "priority": GroupSpec(nullable=False),
    "type": GroupSpec(nullable=False),
    "sprint": GroupSpec(),
    "labels": GroupSpec(multi_valued=True),
}


@dataclass(frozen=True, slots=True)
class Bucket:
    """칸 하나.

    `key` 가 `None` 이면 "값이 없는 것들" 이다 — 빈 칸이 아니라 하나의 칸이다.
    """

    key: str | None
    count: int


@dataclass(slots=True)
class CountReport:
    group_by: str
    buckets: list[Bucket]
    #: 조건에 맞는 이슈 수. **단일 값 기준이면 칸의 합과 같아야 한다.**
    total: int
    #: 이슈 하나가 여러 칸에 들어갈 수 있나. 그러면 합 > 총계다.
    multi_valued: bool
    #: `MAX_BUCKETS` 에서 잘렸나.
    truncated: bool


class ReportService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def count(self, actor: Actor, *, iql: str, group_by: str) -> CountReport:
        """IQL 이 고른 것을 기준별로 센다.

        **검색과 같은 조건을 쓴다.** 세는 길이 따로 조건을 만들면 언젠가
        어긋나고, 그때 리포트와 목록이 다른 숫자를 말한다.
        """
        key = group_by.strip().lower()
        spec = GROUPS.get(key)
        if spec is None:
            raise ValidationError(
                "그 기준으로는 셀 수 없다.",
                code="issues.report_unknown_group",
                details={"group_by": group_by, "supported": sorted(GROUPS)},
            )

        acl = await self._perms.acl_for(self._s, actor, perms.ISSUE_VIEW)
        query = parse(iql)
        compiled = compile_query(
            query,
            acl=acl,
            ctx=FunctionContext(actor_id=actor.user_id, timezone=actor.timezone),
            project_ids=await self._resolve_projects(query),
        )

        total = await self._total(compiled)
        column, needs = _grouping(key)
        stmt: Select[Any] = compiled.apply_for_aggregate(
            select(column.label("bucket"), func.count(Issue.id).label("n")),
            extra_joins=needs,
        )
        # 큰 칸부터. 같으면 이름순이라 순서가 실행마다 흔들리지 않는다 —
        # 흔들리면 같은 리포트를 두 번 열었을 때 다르게 보인다.
        stmt = stmt.group_by(column).order_by(func.count(Issue.id).desc(), column)

        rows = list((await self._s.execute(stmt.limit(MAX_BUCKETS + 1))).all())
        truncated = len(rows) > MAX_BUCKETS
        buckets = [
            Bucket(key=None if row.bucket is None else str(row.bucket), count=int(row.n))
            for row in rows[:MAX_BUCKETS]
        ]
        return CountReport(
            group_by=key,
            buckets=buckets,
            total=total,
            multi_valued=spec.multi_valued,
            truncated=truncated,
        )

    async def _total(self, compiled: Any) -> int:
        """조건에 맞는 **이슈 수**.

        묶기용 조인(`extra_joins`)을 **일부러 안 넘긴다.** 라벨로 묶으려고
        건 조인이 여기 들어오면 라벨 수만큼 행이 곱해져서, 총계가 칸의 합보다
        커진다. 그게 이 값이 맞는 첫째 이유다.

        `DISTINCT` 는 그 위에 덧댄 안전장치다. 오늘 컴파일러가 거는 조인은
        전부 다대일(`status`·`type`·`sprint`·`parent`)이라 곱해지지 않지만,
        일대다 조인이 하나라도 조건에 생기는 날 이 값이 조용히 커진다 —
        그날을 시험으로 잡을 방법이 지금은 없으므로 값으로 막아 둔다.
        """
        stmt = compiled.apply_for_aggregate(select(func.count(func.distinct(Issue.id))))
        found = (await self._s.execute(stmt)).scalar_one_or_none()
        return int(found or 0)

    async def _resolve_projects(self, query: Any) -> dict[str, UUID]:
        out: dict[str, UUID] = {}
        for key in project_keys_in(query):
            found = await org.get_project_by_key(self._s, key)
            if found is not None:
                out[key] = found.id
        return out


def _grouping(key: str) -> tuple[SQLColumnExpression[Any], tuple[str, ...]]:
    """묶을 열과, 그 열을 얻기 위해 필요한 조인 이름.

    이름으로 넘기는 이유: 컴파일러가 조건 때문에 **이미 걸었을 수 있다.**
    여기서 또 걸면 같은 테이블이 두 번 들어가 행이 곱해지고, 세는 값이
    조용히 커진다. `apply_for_aggregate` 가 겹치는 것을 한 번만 건다.
    """
    match key:
        case "status":
            return WorkflowState.name, ("status",)
        case "statuscategory":
            return WorkflowState.category, ("status",)
        case "type":
            return IssueType.name, ("type",)
        case "sprint":
            return Sprint.name, ("sprint",)
        case "labels":
            return GROUPED_LABEL.label, ("labels",)
        case "assignee":
            return Issue.assignee_id, ()
        case "reporter":
            return Issue.reporter_id, ()
        case _:
            return Issue.priority, ()


__all__ = ["GROUPS", "MAX_BUCKETS", "Bucket", "CountReport", "GroupSpec", "ReportService"]
