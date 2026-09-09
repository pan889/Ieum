"""스프린트 라우터 (M5)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.issues.sprints import (
    MAX_ACTIVE,
    ActiveSprint,
    BurndownPoint,
    SprintService,
    SprintView,
)

sprints_router = APIRouter(prefix="/sprints", tags=["sprints"])


class SprintResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    goal: str | None
    state: str
    starts_at: datetime | None
    ends_at: datetime | None
    activated_at: datetime | None
    closed_at: datetime | None
    #: 지금 들어 있는 양. 목록에서 바로 읽혀야 한다 — 스프린트를 열어 봐야
    #: 크기를 아는 것은 계획 회의에서 쓸 수 없다.
    issues: int
    minutes: int
    remaining_issues: int
    remaining_minutes: int

    @classmethod
    def of(cls, view: SprintView) -> SprintResponse:
        row = view.sprint
        return cls(
            id=row.id,
            project_id=row.project_id,
            name=row.name,
            goal=row.goal,
            state=row.state,
            starts_at=row.starts_at,
            ends_at=row.ends_at,
            activated_at=row.activated_at,
            closed_at=row.closed_at,
            issues=view.totals.issues,
            minutes=view.totals.minutes,
            remaining_issues=view.totals.remaining_issues,
            remaining_minutes=view.totals.remaining_minutes,
        )


class ActiveSprintResponse(SprintResponse):
    """도는 스프린트 + 어느 프로젝트의 것인가.

    첫 화면이 여러 프로젝트를 섞어 보여 주므로 프로젝트 키와 이름이 함께
    나가야 한다 — 화면이 행마다 프로젝트를 물어보면 목록 길이만큼 왕복이
    늘어난다.
    """

    project_key: str
    project_name: str

    @classmethod
    def of_active(cls, found: ActiveSprint) -> ActiveSprintResponse:
        base = SprintResponse.of(found.view)
        return cls(
            **base.model_dump(),
            project_key=found.project.key,
            project_name=found.project.name,
        )


class BurndownPointResponse(BaseModel):
    on_date: str
    remaining_issues: int
    remaining_minutes: int
    total_issues: int
    total_minutes: int

    @classmethod
    def of(cls, point: BurndownPoint) -> BurndownPointResponse:
        return cls(
            on_date=point.on_date.isoformat(),
            remaining_issues=point.remaining_issues,
            remaining_minutes=point.remaining_minutes,
            total_issues=point.total_issues,
            total_minutes=point.total_minutes,
        )


class SprintCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    name: str = Field(min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None


class SprintUpdateRequest(BaseModel):
    """고칠 것만 보낸다.

    `null` 을 "값 없음" 과 구분할 방법이 JSON 에 없어서, 지우는 것은 플래그로
    말한다 — 보드(`clear_swimlane`)와 같은 방식이다. 안 그러면 기간을 지우려는
    요청이 조용히 성공하고 아무 일도 안 한다.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    goal: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    clear_goal: bool = False
    clear_starts_at: bool = False
    clear_ends_at: bool = False


class SprintCloseRequest(BaseModel):
    """닫을 때 **남은 것을 어디로 보낼지 반드시 말한다.**

    기본값을 두지 않는 이유: 조용히 백로그로 흘려보내면 "다음에 하기로 했던
    것" 이 아무도 안 보는 곳으로 간다.
    """

    model_config = ConfigDict(extra="forbid")

    move_to: UUID | None = None
    to_backlog: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> SprintCloseRequest:
        if (self.move_to is None) == (not self.to_backlog):
            raise ValueError("move_to 와 to_backlog 중 하나만 골라야 한다")
        return self


class SprintAssignRequest(BaseModel):
    """이슈를 넣거나 뺀다. `sprint_id` 가 `null` 이면 백로그로."""

    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    sprint_id: UUID | None = None
    issue_ids: list[UUID] = Field(min_length=1, max_length=200)


@sprints_router.get("", response_model=list[SprintResponse])
async def list_sprints(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, project_id: UUID
) -> list[SprintResponse]:
    rows = await SprintService(session, permissions).list_for(actor, project_id)
    return [SprintResponse.of(row) for row in rows]


#: **`/{sprint_id}` 형제 전부보다 위에 있어야 한다.** FastAPI 는 먼저 선언된
#: 라우트를 쓰므로, 아래로 내려가면 `mine` 이 스프린트 id 로 잡혀 UUID 파싱
#: 오류가 난다 (conventions "고정 경로는 형제 전부보다 위에 둔다").
@sprints_router.get("/mine", response_model=list[ActiveSprintResponse])
async def list_my_sprints(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    limit: int = Query(default=MAX_ACTIVE, ge=1, le=MAX_ACTIVE),
) -> list[ActiveSprintResponse]:
    """지금 도는 스프린트 중 **내 일이 들어 있는 것.** 첫 화면이 쓴다."""
    rows = await SprintService(session, permissions).list_mine(actor, limit=limit)
    return [ActiveSprintResponse.of_active(row) for row in rows]


@sprints_router.post("", response_model=SprintResponse, status_code=status.HTTP_201_CREATED)
async def create_sprint(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: SprintCreateRequest,
) -> SprintResponse:
    service = SprintService(session, permissions)
    row = await service.create(
        actor,
        project_id=payload.project_id,
        name=payload.name,
        goal=payload.goal,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
    )
    await session.commit()
    return SprintResponse.of(await _view(service, actor, row.id, payload.project_id))


@sprints_router.patch("/{sprint_id}", response_model=SprintResponse)
async def update_sprint(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    sprint_id: UUID,
    payload: SprintUpdateRequest,
) -> SprintResponse:
    service = SprintService(session, permissions)
    row = await service.update(
        actor,
        sprint_id,
        name=payload.name,
        goal=payload.goal,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        clear_goal=payload.clear_goal,
        clear_starts_at=payload.clear_starts_at,
        clear_ends_at=payload.clear_ends_at,
    )
    await session.commit()
    return SprintResponse.of(await _view(service, actor, row.id, row.project_id))


@sprints_router.delete("/{sprint_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sprint(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, sprint_id: UUID
) -> None:
    await SprintService(session, permissions).delete(actor, sprint_id)
    await session.commit()


@sprints_router.post("/{sprint_id}/start", response_model=SprintResponse)
async def start_sprint(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, sprint_id: UUID
) -> SprintResponse:
    service = SprintService(session, permissions)
    row = await service.start(actor, sprint_id)
    await session.commit()
    return SprintResponse.of(await _view(service, actor, row.id, row.project_id))


@sprints_router.post("/{sprint_id}/close", response_model=SprintResponse)
async def close_sprint(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    sprint_id: UUID,
    payload: SprintCloseRequest,
) -> SprintResponse:
    service = SprintService(session, permissions)
    row = await service.close(
        actor, sprint_id, move_to=payload.move_to, to_backlog=payload.to_backlog
    )
    await session.commit()
    return SprintResponse.of(await _view(service, actor, row.id, row.project_id))


@sprints_router.get("/{sprint_id}/burndown", response_model=list[BurndownPointResponse])
async def sprint_burndown(
    session: DbSession, permissions: PermissionDep, actor: CurrentActor, sprint_id: UUID
) -> list[BurndownPointResponse]:
    rows = await SprintService(session, permissions).burndown(actor, sprint_id)
    return [BurndownPointResponse.of(row) for row in rows]


@sprints_router.post("/issues", response_model=dict[str, int])
async def assign_issues(
    session: DbSession,
    permissions: PermissionDep,
    actor: CurrentActor,
    payload: SprintAssignRequest,
) -> dict[str, int]:
    moved = await SprintService(session, permissions).assign_issues(
        actor,
        sprint_id=payload.sprint_id,
        issue_ids=payload.issue_ids,
        project_id=payload.project_id,
    )
    await session.commit()
    return {"moved": moved}


async def _view(
    service: SprintService, actor: CurrentActor, sprint_id: UUID, project_id: UUID
) -> SprintView:
    """방금 만지고 나서 합계까지 담아 돌려준다.

    목록을 다시 부르는 것은 낭비지만, 합계는 서비스가 세는 값이라 여기서
    손으로 만들면 두 벌이 된다 — 그리고 두 벌은 어긋난다.
    """
    for row in await service.list_for(actor, project_id):
        if row.sprint.id == sprint_id:
            return row
    raise AssertionError("방금 만진 스프린트가 목록에 없다")
