"""org 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreateRequest(BaseModel):
    key: str = Field(min_length=2, max_length=16)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    parent_id: UUID | None = None
    lead_id: UUID | None = None
    is_public: bool = False


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    description: str | None
    parent_id: UUID | None
    lead_id: UUID | None
    is_public: bool
    archived_at: datetime | None
    created_at: datetime


class ProjectPageResponse(BaseModel):
    items: list[ProjectResponse]
    next_cursor: str | None = None
    total: int | None = None


class RoleCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scope_kind: str = Field(pattern="^(global|project|space|queue)$")
    description: str | None = Field(default=None, max_length=1000)
    grants: list[str] = Field(default_factory=list)


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    scope_kind: str
    is_builtin: bool


class RoleAssignRequest(BaseModel):
    role_id: UUID
    scope_kind: str = Field(pattern="^(global|project|space|queue)$")
    scope_id: UUID | None = None
    principal_kind: str = Field(pattern="^(user|group)$")
    principal_id: UUID


class PermissionDefResponse(BaseModel):
    key: str
    description: str
    scope_kinds: list[str]
    requires_step_up: bool
