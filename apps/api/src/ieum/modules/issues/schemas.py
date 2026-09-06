"""issues 요청·응답 스키마."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from ieum.modules.issues.models import LINK_KINDS


class IssueCreateRequest(BaseModel):
    project_id: UUID
    summary: str = Field(min_length=1, max_length=500)
    type_id: UUID | None = None
    description: str | None = Field(default=None, max_length=100_000)
    assignee_id: UUID | None = None
    priority: int = Field(default=3, ge=1, le=5)
    parent_id: UUID | None = None
    due_date: date | None = None
    labels: list[str] = Field(default_factory=list, max_length=30)
    custom_fields: dict[str, Any] = Field(default_factory=dict)


class IssueUpdateRequest(BaseModel):
    """부분 수정. 미포함과 null 을 구분한다 (conventions.md API 규약).

    `changes` 에 키가 없으면 '건드리지 않음', null 이면 '비움'이다.

    extra="forbid" 다. `{"summary": "..."}` 처럼 changes 를 빼먹은 요청을
    조용히 무시하면 클라이언트는 200 을 받고 아무것도 안 바뀐 걸 모른다.
    """

    model_config = ConfigDict(extra="forbid")

    changes: dict[str, Any] = Field(default_factory=dict)
    labels: list[str] | None = None
    custom_fields: dict[str, Any] | None = None


class TransitionRequest(BaseModel):
    transition_id: UUID
    inputs: dict[str, Any] = Field(default_factory=dict)


class LinkRequest(BaseModel):
    to_issue_id: UUID
    kind: str = Field(pattern=f"^({'|'.join(LINK_KINDS)})$")


class CommentCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=100_000)
    is_internal: bool = False


class CommentUpdateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=100_000)


class IssueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    project_id: UUID
    summary: str
    description: str | None
    type_id: UUID
    type_name: str
    state_id: UUID
    state_name: str
    state_category: str
    reporter_id: UUID | None
    assignee_id: UUID | None
    priority: int
    parent_id: UUID | None
    start_date: date | None
    due_date: date | None
    estimate_minutes: int | None
    progress: int
    resolved_at: datetime | None
    archived_at: datetime | None
    version: int
    labels: list[str]
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IssueSummaryResponse(BaseModel):
    """목록용. 상세보다 가볍다.

    key 와 상태 이름이 들어 있다 — 클라이언트가 프로젝트·상태 목록을 따로
    받아 이어 붙이게 하면, 그 목록에 없는 이슈는 키도 상태도 못 그린다.
    """

    id: UUID
    key: str
    key_seq: int
    project_id: UUID
    summary: str
    state_id: UUID
    state_name: str
    state_category: str
    assignee_id: UUID | None
    priority: int
    due_date: date | None
    updated_at: datetime


class IssuePageResponse(BaseModel):
    items: list[IssueSummaryResponse]
    next_cursor: str | None = None
    total: int | None = None


class TransitionResponse(BaseModel):
    id: UUID
    name: str
    to_state_id: UUID
    to_state_name: str
    blocked_by: list[str]


class CommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    issue_id: UUID
    author_id: UUID | None
    body: str
    is_internal: bool
    edited_at: datetime | None
    created_at: datetime


class HistoryEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    actor_id: UUID | None
    changes: list[dict[str, Any]]
    created_at: datetime


class FieldDefinitionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    kind: str
    description: str | None
    config: dict[str, Any]
    is_required: bool
    position: int


class VersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    start_date: date | None
    release_date: date | None
    status: str


class IssueTypeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    icon: str | None
    is_subtask: bool
    workflow_id: UUID
    position: int


class WorkflowStateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    category: str
    position: int
    is_initial: bool


class WorklogCreateRequest(BaseModel):
    """작업 로그.

    분 단위 정수만 받는다. "2h 30m" 같은 사람용 표기는 클라이언트가 파싱해서
    분으로 바꿔 보낸다 — 서버가 두 형식을 다 받으면 둘이 어긋날 때 어느 쪽이
    이기는지 규칙이 하나 더 생긴다.
    """

    spent_minutes: int = Field(gt=0)
    work_date: date | None = None
    comment: str | None = Field(default=None, max_length=2000)


class WorklogUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spent_minutes: int | None = Field(default=None, gt=0)
    work_date: date | None = None
    comment: str | None = Field(default=None, max_length=2000)
    #: null 을 "값 없음" 과 구분할 방법이 JSON 에 없다. 지우려면 이 플래그를 쓴다.
    clear_comment: bool = False


class WorklogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    issue_id: UUID
    user_id: UUID | None
    spent_minutes: int
    work_date: date
    comment: str | None
    created_at: datetime


class TimeSummaryResponse(BaseModel):
    estimate_minutes: int | None
    spent_minutes: int
    remaining_minutes: int | None
    over_estimate: bool


class WorklogPanelResponse(BaseModel):
    """이슈 상세의 시간 패널. 목록과 합계를 한 번에 준다."""

    summary: TimeSummaryResponse
    items: list[WorklogResponse]


class RelatedIssueResponse(BaseModel):
    link_id: UUID
    kind: str
    #: True 면 이 이슈가 관계의 출발점이다 ("blocks" vs "blocked by").
    outward: bool
    issue: IssueSummaryResponse


class IssueRelationsResponse(BaseModel):
    parent: IssueSummaryResponse | None
    children: list[IssueSummaryResponse]
    links: list[RelatedIssueResponse]


class BulkEditRequest(BaseModel):
    """일괄 편집. 부분 성공을 허용한다."""

    model_config = ConfigDict(extra="forbid")

    issue_ids: list[UUID] = Field(min_length=1, max_length=100)
    changes: dict[str, Any] = Field(default_factory=dict)
    add_labels: list[str] | None = None
    remove_labels: list[str] | None = None
    transition_id: UUID | None = None


class BulkFailureResponse(BaseModel):
    issue_id: UUID
    code: str
    message: str


class BulkEditResponse(BaseModel):
    updated: list[UUID]
    failed: list[BulkFailureResponse]
