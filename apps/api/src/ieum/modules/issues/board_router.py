"""칸반 보드 라우터."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Any, Literal, get_args
from uuid import UUID

from fastapi import APIRouter, Header, status
from pydantic import BaseModel, ConfigDict, Field

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.modules.issues.boards import (
    COLUMN_PAGE_SIZE,
    MAX_COLUMNS,
    SWIMLANE_FIELDS,
    BoardService,
    ColumnResult,
    Swimlane,
)
from ieum.modules.issues.models import Board
from ieum.modules.issues.service import IssueView

boards_router = APIRouter(prefix="/boards", tags=["boards"])

IfMatch = Annotated[str | None, Header(alias="If-Match")]


def _parse_if_match(raw: str | None) -> int | None:
    if raw is None:
        return None
    value = raw.strip().strip('"')
    return int(value) if value.isdigit() else None


#: 스윔레인 기준은 열린 문자열이 아니다. 모르는 값이 저장되면 보드를 열 때
#: 터지므로 요청 단계에서 거절한다. `Literal` 은 변수를 못 받아 손으로 적고,
#: 아래 단언으로 서비스의 목록과 어긋나지 않게 묶어 둔다.
SwimlaneField = Literal["assignee", "priority", "type"]
assert set(get_args(SwimlaneField)) == set(SWIMLANE_FIELDS)


class BoardColumnPayload(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    #: 빈 문자열이면 보드 범위 전체. 컬럼 하나짜리 백로그 보드에 쓴다.
    iql: str = Field(default="", max_length=4000)
    wip_limit: int | None = Field(default=None, ge=1, le=COLUMN_PAGE_SIZE)


class BoardCreateRequest(BaseModel):
    project_id: UUID
    name: str = Field(min_length=1, max_length=200)
    columns: list[BoardColumnPayload] = Field(min_length=1, max_length=MAX_COLUMNS)
    swimlane_by: SwimlaneField | None = None
    base_iql: str | None = Field(default=None, max_length=4000)


class BoardUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    columns: list[BoardColumnPayload] | None = Field(default=None, max_length=MAX_COLUMNS)
    swimlane_by: SwimlaneField | None = None
    base_iql: str | None = Field(default=None, max_length=4000)
    #: null 을 "값 없음" 과 구분할 방법이 JSON 에 없다. 지우려면 이 플래그를 쓴다.
    clear_swimlane: bool = False
    clear_base_iql: bool = False


class BoardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    name: str
    columns: list[dict[str, Any]]
    swimlane_by: str | None
    base_iql: str | None
    position: int


class BoardCardResponse(BaseModel):
    """보드 카드. 상세 응답보다 가볍다 — 컬럼 12개에 50장씩 한 번에 나간다."""

    id: UUID
    key: str
    summary: str
    type_id: UUID
    type_name: str
    state_id: UUID
    state_name: str
    state_category: str
    assignee_id: UUID | None
    priority: int
    due_date: date | None
    labels: list[str]
    #: 드래그 전이 때 If-Match 로 되돌려 준다.
    version: int


class BoardColumnResponse(BaseModel):
    name: str
    iql: str
    wip_limit: int | None
    issues: list[BoardCardResponse]
    loaded: int
    truncated: bool
    over_wip: bool


class BoardSwimlaneResponse(BaseModel):
    """보드의 가로 줄. 스윔레인이 없으면 `key` 가 빈 레인 하나만 온다."""

    key: str
    #: 표시 이름. priority 는 키("1"~"5")가 그대로 온다 — 번역은 화면이 한다.
    label: str
    columns: list[BoardColumnResponse]


class BoardContentResponse(BaseModel):
    board: BoardResponse
    lanes: list[BoardSwimlaneResponse]


class BoardMoveRequest(BaseModel):
    issue_id: UUID
    transition_id: UUID


def _card(view: IssueView) -> BoardCardResponse:
    issue = view.issue
    return BoardCardResponse(
        id=issue.id,
        key=view.key,
        summary=issue.summary,
        type_id=issue.type_id,
        type_name=view.type_name,
        state_id=issue.state_id,
        state_name=view.state_name,
        state_category=view.state_category,
        assignee_id=issue.assignee_id,
        priority=issue.priority,
        due_date=issue.due_date,
        labels=view.labels,
        version=issue.version,
    )


def _column(result: ColumnResult) -> BoardColumnResponse:
    return BoardColumnResponse(
        name=result.name,
        iql=result.iql,
        wip_limit=result.wip_limit,
        issues=[_card(v) for v in result.issues],
        loaded=result.loaded,
        truncated=result.truncated,
        over_wip=result.over_wip,
    )


def _lane(lane: Swimlane) -> BoardSwimlaneResponse:
    return BoardSwimlaneResponse(
        key=lane.key,
        label=lane.label,
        columns=[_column(c) for c in lane.columns],
    )


def _board(board: Board) -> BoardResponse:
    return BoardResponse.model_validate(board)


@boards_router.post("", response_model=BoardResponse, status_code=status.HTTP_201_CREATED)
async def create_board(
    body: BoardCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> BoardResponse:
    board = await BoardService(session, permissions).create(
        actor,
        project_id=body.project_id,
        name=body.name,
        columns=[c.model_dump() for c in body.columns],
        swimlane_by=body.swimlane_by,
        base_iql=body.base_iql,
    )
    await session.commit()
    return _board(board)


@boards_router.get("", response_model=list[BoardResponse])
async def list_boards(
    project_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[BoardResponse]:
    rows = await BoardService(session, permissions).list_for_project(actor, project_id)
    return [_board(r) for r in rows]


@boards_router.get("/{board_id}", response_model=BoardResponse)
async def get_board(
    board_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> BoardResponse:
    return _board(await BoardService(session, permissions).get(actor, board_id))


@boards_router.patch("/{board_id}", response_model=BoardResponse)
async def update_board(
    board_id: UUID,
    body: BoardUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> BoardResponse:
    board = await BoardService(session, permissions).update(
        actor,
        board_id,
        name=body.name,
        columns=None if body.columns is None else [c.model_dump() for c in body.columns],
        swimlane_by=body.swimlane_by,
        base_iql=body.base_iql,
        clear_swimlane=body.clear_swimlane,
        clear_base_iql=body.clear_base_iql,
    )
    await session.commit()
    return _board(board)


@boards_router.delete("/{board_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_board(
    board_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await BoardService(session, permissions).delete(actor, board_id)
    await session.commit()


@boards_router.get("/{board_id}/content", response_model=BoardContentResponse)
async def load_board(
    board_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> BoardContentResponse:
    """컬럼별 이슈. 컬럼마다 IQL 질의가 한 번씩 나간다."""
    service = BoardService(session, permissions)
    board = await service.get(actor, board_id)
    lanes = await service.load(actor, board_id)
    return BoardContentResponse(board=_board(board), lanes=[_lane(lane) for lane in lanes])


@boards_router.post("/{board_id}/move", response_model=BoardCardResponse)
async def move_card(
    board_id: UUID,
    body: BoardMoveRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    if_match: IfMatch = None,
) -> BoardCardResponse:
    """드래그 전이. 일반 전이와 같은 워크플로우 검증을 탄다."""
    view = await BoardService(session, permissions).move(
        actor,
        board_id,
        body.issue_id,
        body.transition_id,
        expected_version=_parse_if_match(if_match),
    )
    await session.commit()
    return _card(view)


__all__ = ["boards_router"]
