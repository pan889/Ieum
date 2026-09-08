"""달력 라우터 (A18, M5)."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.issues.calendar import (
    CalendarEntry,
    CalendarService,
    CalendarSprint,
    CalendarView,
)

calendar_router = APIRouter(prefix="/calendar", tags=["calendar"])


class CalendarEntryResponse(BaseModel):
    id: UUID
    key: str
    summary: str
    #: 놓이는 첫 날과 마지막 날. 하루짜리는 둘이 같다.
    starts_on: date
    ends_on: date
    state_name: str
    state_category: str
    assignee_id: UUID | None
    priority: int
    overdue: bool

    @classmethod
    def of(cls, entry: CalendarEntry) -> CalendarEntryResponse:
        return cls(
            id=entry.id,
            key=entry.key,
            summary=entry.summary,
            starts_on=entry.starts_on,
            ends_on=entry.ends_on,
            state_name=entry.state_name,
            state_category=entry.state_category,
            assignee_id=entry.assignee_id,
            priority=entry.priority,
            overdue=entry.overdue,
        )


class CalendarSprintResponse(BaseModel):
    id: UUID
    name: str
    state: str
    #: **시각 그대로** 준다. 날짜로 접으려면 타임존을 골라야 하고, 그 선택은
    #: 보는 사람마다 다르다 — 화면이 접는다.
    starts_at: datetime | None
    ends_at: datetime | None

    @classmethod
    def of(cls, row: CalendarSprint) -> CalendarSprintResponse:
        return cls(
            id=row.id,
            name=row.name,
            state=row.state,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
        )


class CalendarResponse(BaseModel):
    starts_on: date
    ends_on: date
    entries: list[CalendarEntryResponse]
    sprints: list[CalendarSprintResponse]
    #: 질의에는 맞지만 **날짜가 없어서 놓을 수 없는** 이슈 수.
    #:
    #: 0 이 아니면 화면은 그것을 적어야 한다. 조용히 빼면 사람은 "이번 주는
    #: 비어 있다" 고 읽는데, 실제로는 날짜만 안 적힌 일이 스무 건 있다.
    undated: int
    #: 상한에서 잘렸나. 잘린 달력을 다 그린 달력으로 읽으면 없는 여유를
    #: 있다고 계획한다.
    truncated: bool

    @classmethod
    def of(cls, view: CalendarView) -> CalendarResponse:
        return cls(
            starts_on=view.starts_on,
            ends_on=view.ends_on,
            entries=[CalendarEntryResponse.of(e) for e in view.entries],
            sprints=[CalendarSprintResponse.of(s) for s in view.sprints],
            undated=view.undated,
            truncated=view.truncated,
        )


@calendar_router.get("", response_model=CalendarResponse)
async def read_calendar(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    project_id: UUID,
    starts_on: date,
    ends_on: date,
    iql: str | None = Query(default=None, max_length=4000),
) -> CalendarResponse:
    """창 하나.

    기간에는 상한이 있다(`calendar.MAX_DAYS`). 넘으면 422 이고, 오류 메시지가
    며칠까지인지 말한다 — 숫자를 여기 한 번 더 적으면 두 곳이 어긋난다.

    `iql` 은 프로젝트 범위 **안에서 더 좁히는** 조건이다 — 보드와 같은 방식
    으로 AND 로 묶는다(D-44). 달력 전용 필터 포맷을 만들지 않는다.
    """
    view = await CalendarService(session, permissions).window(
        actor,
        project_id=project_id,
        starts_on=starts_on,
        ends_on=ends_on,
        iql=iql,
    )
    return CalendarResponse.of(view)


__all__ = ["calendar_router"]
