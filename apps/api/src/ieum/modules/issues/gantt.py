"""간트 (A17, M5).

달력이 "언제인가" 라면 간트는 **"무엇 다음인가"** 다. 의존이 없으면 간트는
가로로 누운 달력일 뿐이므로, 여기서 새로 다루는 것은 `precedes` 링크다.

## 모순된 의존을 조용히 그리지 않는다

`A precedes B` 인데 B 가 A 보다 먼저 시작하면 그건 **일정 충돌**이다. 겹친
막대로 그려 놓고 넘어가면 아무도 모른다 — 사람은 화살표가 있으니 순서가
지켜진다고 읽는다. 그래서 어긋난 링크를 세어 `conflicts` 로 돌려준다.

## 창 밖을 가리키는 의존은 그리지 않고 센다

**권한 때문이다.** 창 안의 이슈들은 `issue.view` ACL 을 타고 왔다. 링크
상대가 그 집합 밖이면 그 이슈를 볼 수 있는지 확인하지 않았고, 날짜를 읽어
충돌을 계산하면 **안 보여야 할 이슈의 일정이 새어 나간다.**

그러니 그리지 않는다. 대신 몇 개가 밖을 가리키는지 말한다 — 사람은 창을
넓혀서 보면 된다.

## 날짜 규칙은 달력과 같은 코드를 쓴다

`span_of`·`_overlaps`·창 검사를 `calendar` 에서 그대로 가져온다. 두 벌이면
어긋나고, 어긋나면 같은 이슈가 달력에는 있고 간트에는 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.permissions import PermissionService
from ieum.modules.issues.calendar import CalendarEntry, CalendarService, CalendarSprint
from ieum.modules.issues.models import IssueLink

#: 간트가 다루는 링크 종류. `blocks` 도 순서를 뜻하는 것처럼 보이지만 시간
#: 순서가 아니라 **상태 조건**이다("이게 안 끝나면 저걸 못 한다" 는 날짜와
#: 별개다). 간트의 화살표는 시간축 위의 화살표여야 한다.
LINK_KIND = "precedes"


@dataclass(frozen=True, slots=True)
class GanttRow:
    """막대 하나. 달력의 칸과 같은 값에 **의존**이 붙는다."""

    entry: CalendarEntry
    #: 이 이슈보다 **먼저 와야 하는** 것들. 창 안에 있는 것만 담는다.
    depends_on: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class GanttConflict:
    """`predecessor precedes successor` 인데 날짜가 그 순서를 안 지킨다."""

    predecessor: UUID
    successor: UUID
    #: 며칠 어긋났나. 1 이면 후행이 선행이 끝나는 날에 시작한다(하루 겹침).
    overlap_days: int


@dataclass(slots=True)
class GanttView:
    starts_on: date
    ends_on: date
    rows: list[GanttRow] = field(default_factory=list)
    sprints: list[CalendarSprint] = field(default_factory=list)
    #: **어긋난 의존.** 비어 있지 않으면 화면은 그것을 눈에 띄게 적어야 한다.
    conflicts: list[GanttConflict] = field(default_factory=list)
    #: 날짜가 없어 막대를 그릴 수 없는 이슈 수 (달력과 같은 이유).
    undated: int = 0
    truncated: bool = False
    #: 창 밖을 가리켜 **그릴 수 없는** 의존 수. 권한을 확인하지 않은 이슈의
    #: 일정을 읽지 않기 위해 그리지 않는다.
    links_outside: int = 0


class GanttService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def window(
        self,
        actor: Actor,
        *,
        project_id: UUID,
        starts_on: date,
        ends_on: date,
        iql: str | None = None,
        today: date | None = None,
    ) -> GanttView:
        """창 하나. 막대는 **먼저 시작하는 것부터** 놓는다.

        간트는 위에서 아래로 읽는 그림이고, 순서가 시간이 아니면 화살표가
        아래에서 위로 거슬러 올라간다 — 그러면 읽을 수 없다.
        """
        base = await CalendarService(self._s, self._perms).window(
            actor,
            project_id=project_id,
            starts_on=starts_on,
            ends_on=ends_on,
            iql=iql,
            today=today,
        )
        ordered = sorted(base.entries, key=lambda e: (e.starts_on, e.ends_on, e.key))
        inside = {e.id: e for e in ordered}

        links, outside = await self._links(set(inside))
        depends: dict[UUID, list[UUID]] = {row_id: [] for row_id in inside}
        conflicts: list[GanttConflict] = []
        for predecessor, successor in links:
            depends[successor].append(predecessor)
            overlap = _overlap_days(inside[predecessor], inside[successor])
            if overlap > 0:
                conflicts.append(
                    GanttConflict(
                        predecessor=predecessor, successor=successor, overlap_days=overlap
                    )
                )

        return GanttView(
            starts_on=base.starts_on,
            ends_on=base.ends_on,
            rows=[
                GanttRow(entry=entry, depends_on=tuple(sorted(depends[entry.id], key=str)))
                for entry in ordered
            ],
            sprints=base.sprints,
            conflicts=conflicts,
            undated=base.undated,
            truncated=base.truncated,
            links_outside=outside,
        )

    async def _links(self, ids: set[UUID]) -> tuple[list[tuple[UUID, UUID]], int]:
        """창 안의 `precedes` 링크와, 밖을 가리키는 링크 수.

        한쪽 끝이라도 걸리는 링크를 다 읽고 여기서 가른다. 안쪽만 묻는
        질의로는 "밖을 가리킨다" 를 셀 수 없고, 그럼 화면이 화살표가 없는
        이유를 말할 수 없다.
        """
        if not ids:
            return [], 0
        rows = list(
            (
                await self._s.execute(
                    select(IssueLink).where(
                        IssueLink.kind == LINK_KIND,
                        or_(
                            IssueLink.from_issue_id.in_(ids),
                            IssueLink.to_issue_id.in_(ids),
                        ),
                    )
                )
            )
            .scalars()
            .all()
        )
        drawable: list[tuple[UUID, UUID]] = []
        outside = 0
        for link in rows:
            if link.from_issue_id in ids and link.to_issue_id in ids:
                drawable.append((link.from_issue_id, link.to_issue_id))
            else:
                outside += 1
        return drawable, outside


def _overlap_days(predecessor: CalendarEntry, successor: CalendarEntry) -> int:
    """후행이 선행보다 얼마나 일찍 시작하나. 0 이하면 순서가 지켜진 것이다.

    선행이 끝나는 **다음 날**부터가 정상이다. 같은 날 끝나고 시작하는 것도
    겹침으로 센다 — 하루를 둘로 쪼개 쓰는 계획은 간트가 표현할 수 없고,
    "겹치지 않는다" 고 말해 주면 그 사실이 숨는다.
    """
    return (predecessor.ends_on - successor.starts_on).days + 1


__all__ = [
    "LINK_KIND",
    "GanttConflict",
    "GanttRow",
    "GanttService",
    "GanttView",
]
