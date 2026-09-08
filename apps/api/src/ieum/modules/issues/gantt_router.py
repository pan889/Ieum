"""간트 라우터 (A17, M5)."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.issues.calendar_router import CalendarEntryResponse, CalendarSprintResponse
from ieum.modules.issues.gantt import GanttConflict, GanttRow, GanttService, GanttView

gantt_router = APIRouter(prefix="/gantt", tags=["gantt"])


class GanttRowResponse(CalendarEntryResponse):
    """막대 하나. 달력 칸과 **같은 모양**에 의존이 붙는다.

    상속으로 붙이는 이유: 두 화면이 같은 값을 다르게 부르면 클라이언트가
    변환 코드를 하나 더 들고 다니게 되고, 그 코드가 어긋나는 날이 온다.
    """

    #: 이 이슈보다 먼저 와야 하는 것들. **창 안에 있는 것만** 온다.
    depends_on: list[UUID]

    @classmethod
    def of_row(cls, row: GanttRow) -> GanttRowResponse:
        base = CalendarEntryResponse.of(row.entry)
        return cls(**base.model_dump(), depends_on=list(row.depends_on))


class GanttConflictResponse(BaseModel):
    """`predecessor precedes successor` 인데 날짜가 그 순서를 안 지킨다."""

    predecessor: UUID
    successor: UUID
    #: 며칠 어긋났나. 1 이면 후행이 선행이 끝나는 날에 시작한다.
    overlap_days: int

    @classmethod
    def of(cls, row: GanttConflict) -> GanttConflictResponse:
        return cls(
            predecessor=row.predecessor,
            successor=row.successor,
            overlap_days=row.overlap_days,
        )


class GanttResponse(BaseModel):
    starts_on: date
    ends_on: date
    #: **먼저 시작하는 것부터** 온다. 순서가 시간이 아니면 화살표가 아래에서
    #: 위로 거슬러 올라가 읽을 수 없다.
    rows: list[GanttRowResponse]
    sprints: list[CalendarSprintResponse]
    #: **어긋난 의존.** 비어 있지 않으면 화면은 눈에 띄게 적어야 한다 —
    #: 겹친 막대만 그려 놓으면 사람은 화살표가 있으니 순서가 지켜진다고 읽는다.
    conflicts: list[GanttConflictResponse]
    #: 날짜가 없어 막대를 그릴 수 없는 이슈 수 (달력과 같은 이유).
    undated: int
    truncated: bool
    #: 창 밖을 가리켜 그릴 수 없는 의존 수.
    #:
    #: 권한을 확인하지 않은 이슈의 일정을 읽지 않기 위해 그리지 않는다 —
    #: 창을 넓혀 보면 그 이슈도 ACL 을 타고 들어온다.
    links_outside: int

    @classmethod
    def of(cls, view: GanttView) -> GanttResponse:
        return cls(
            starts_on=view.starts_on,
            ends_on=view.ends_on,
            rows=[GanttRowResponse.of_row(r) for r in view.rows],
            sprints=[CalendarSprintResponse.of(s) for s in view.sprints],
            conflicts=[GanttConflictResponse.of(c) for c in view.conflicts],
            undated=view.undated,
            truncated=view.truncated,
            links_outside=view.links_outside,
        )


@gantt_router.get("", response_model=GanttResponse)
async def read_gantt(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    project_id: UUID,
    starts_on: date,
    ends_on: date,
    iql: str | None = Query(default=None, max_length=4000),
) -> GanttResponse:
    """창 하나. 날짜·범위·상한 규칙은 달력과 **같은 코드**를 쓴다."""
    view = await GanttService(session, permissions).window(
        actor,
        project_id=project_id,
        starts_on=starts_on,
        ends_on=ends_on,
        iql=iql,
    )
    return GanttResponse.of(view)


__all__ = ["gantt_router"]
