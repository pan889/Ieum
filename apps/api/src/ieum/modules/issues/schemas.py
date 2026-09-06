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
    """

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
    due_date: date | None
    progress: int
    resolved_at: datetime | None
    archived_at: datetime | None
    version: int
    labels: list[str]
    custom_fields: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class IssueSummaryResponse(BaseModel):
    """목록용. 상세보다 가볍다."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key_seq: int
    project_id: UUID
    summary: str
    state_id: UUID
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
