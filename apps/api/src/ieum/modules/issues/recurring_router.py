"""반복 이슈 라우터 (A27, M5)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.issues.models import RecurringIssue
from ieum.modules.issues.recurrence import CADENCES, Schedule
from ieum.modules.issues.recurring import NewRecurrence, RecurringIssueService

recurrences_router = APIRouter(prefix="/recurrences", tags=["recurrences"])


class RecurrenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    summary: str
    description: str | None
    type_id: UUID | None
    assignee_id: UUID | None
    priority: int
    labels: list[str]
    due_in_days: int | None

    cadence: str
    hour: int
    minute: int
    weekday: int | None
    day: int | None
    timezone: str

    #: 다음에 도는 시각. **화면이 이것을 보여 줘야** 사람이 켰는데 안 도는
    #: 것을 알 수 있다.
    next_run_at: datetime
    last_run_at: datetime | None
    last_issue_id: UUID | None
    is_enabled: bool
    #: 스스로 껐으면 그 이유 코드. 사람이 끈 것은 비어 있다.
    last_error: str | None

    @classmethod
    def of(cls, row: RecurringIssue) -> RecurrenceResponse:
        return cls.model_validate(row)


class ScheduleBody(BaseModel):
    """언제 도는가. 크론이 아니라 **좁은 어휘**다 (`recurrence.py` 참조)."""

    model_config = ConfigDict(extra="forbid")

    cadence: str
    hour: int = Field(ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    #: **스케줄의 시간대다.** 보는 사람의 것이 아니다.
    timezone: str = Field(min_length=1, max_length=64)
    weekday: int | None = Field(default=None, ge=0, le=6)
    day: int | None = Field(default=None, ge=1, le=31)

    @model_validator(mode="after")
    def _needs_its_part(self) -> ScheduleBody:
        if self.cadence not in CADENCES:
            raise ValueError(f"cadence 는 {', '.join(CADENCES)} 중 하나여야 한다")
        if self.cadence == "weekly" and self.weekday is None:
            raise ValueError("매주 반복에는 weekday 가 필요하다")
        if self.cadence == "monthly" and self.day is None:
            raise ValueError("매월 반복에는 day 가 필요하다")
        return self

    def to_schedule(self) -> Schedule:
        return Schedule(
            cadence=self.cadence,  # type: ignore[arg-type]
            hour=self.hour,
            minute=self.minute,
            timezone=self.timezone,
            weekday=self.weekday,
            day=self.day,
        )


class RecurrenceCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=500)
    schedule: ScheduleBody
    description: str | None = Field(default=None, max_length=100_000)
    type_id: UUID | None = None
    assignee_id: UUID | None = None
    priority: int = Field(default=3, ge=1, le=5)
    labels: list[str] = Field(default_factory=list, max_length=20)
    due_in_days: int | None = Field(default=None, ge=0, le=3650)

    def to_payload(self) -> NewRecurrence:
        return NewRecurrence(
            project_id=self.project_id,
            name=self.name,
            summary=self.summary,
            schedule=self.schedule.to_schedule(),
            description=self.description,
            type_id=self.type_id,
            assignee_id=self.assignee_id,
            priority=self.priority,
            labels=tuple(self.labels),
            due_in_days=self.due_in_days,
        )


class RecurrenceUpdateRequest(RecurrenceCreateRequest):
    """전부 다시 보낸다.

    부분 갱신을 안 받는 이유: 틀과 주기가 한 덩어리이기 때문이다. 주기만
    바꾸는 요청을 허용하면 "요약은 그대로, 주기는 새로" 가 되는데, 그때 다음
    실행 시각을 다시 잡아야 하는지가 요청마다 달라진다 — 지금은 언제나 다시
    잡는다.
    """

    model_config = ConfigDict(extra="forbid")


class RecurrenceEnableRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_enabled: bool


@recurrences_router.get("", response_model=list[RecurrenceResponse])
async def list_recurrences(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, project_id: UUID
) -> list[RecurrenceResponse]:
    rows = await RecurringIssueService(session, permissions).list_for(actor, project_id)
    return [RecurrenceResponse.of(row) for row in rows]


@recurrences_router.post("", response_model=RecurrenceResponse, status_code=status.HTTP_201_CREATED)
async def create_recurrence(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: RecurrenceCreateRequest,
) -> RecurrenceResponse:
    row = await RecurringIssueService(session, permissions).create(actor, payload.to_payload())
    await session.commit()
    return RecurrenceResponse.of(row)


@recurrences_router.put("/{recurrence_id}", response_model=RecurrenceResponse)
async def update_recurrence(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    recurrence_id: UUID,
    payload: RecurrenceUpdateRequest,
) -> RecurrenceResponse:
    row = await RecurringIssueService(session, permissions).update(
        actor, recurrence_id, payload=payload.to_payload()
    )
    await session.commit()
    return RecurrenceResponse.of(row)


@recurrences_router.post("/{recurrence_id}/enabled", response_model=RecurrenceResponse)
async def set_recurrence_enabled(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    recurrence_id: UUID,
    payload: RecurrenceEnableRequest,
) -> RecurrenceResponse:
    """켜고 끈다. **끄기가 지우기와 다르다** — 지우면 왜 멈췄는지도 사라진다."""
    row = await RecurringIssueService(session, permissions).update(
        actor, recurrence_id, is_enabled=payload.is_enabled
    )
    await session.commit()
    return RecurrenceResponse.of(row)


@recurrences_router.delete("/{recurrence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_recurrence(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, recurrence_id: UUID
) -> None:
    await RecurringIssueService(session, permissions).delete(actor, recurrence_id)
    await session.commit()


__all__ = ["recurrences_router"]
