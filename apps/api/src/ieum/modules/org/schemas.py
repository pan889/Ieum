"""org 요청·응답 스키마."""

from __future__ import annotations

from datetime import datetime
from typing import Any
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
    #: 이 역할을 받은 사람에게 2FA 를 강제한다 (auth.md 3절).
    require_mfa: bool = False


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    scope_kind: str
    is_builtin: bool
    require_mfa: bool


class RoleUpdateRequest(BaseModel):
    """보내지 않은 항목은 그대로 둔다.

    권한은 **목록 전체**로 받는다. 추가·제거를 따로 받으면 화면과 서버가
    서로 다른 "지금 상태"를 들고 계산하게 되고, 어긋나는 순간 아무도 모른다.
    """

    grants: list[str] | None = None
    description: str | None = Field(default=None, max_length=1000)
    require_mfa: bool | None = None


class RoleDetailResponse(BaseModel):
    """관리 화면용 역할. 목록에 필요한 곁가지까지 담는다."""

    id: UUID
    name: str
    description: str | None
    scope_kind: str
    is_builtin: bool
    require_mfa: bool
    grants: list[str]
    #: 이 역할을 받은 주체 수. 지우기 전에 영향 범위를 알아야 한다.
    assignment_count: int

    @classmethod
    def of(cls, view: Any) -> RoleDetailResponse:
        return cls(
            id=view.role.id,
            name=view.role.name,
            description=view.role.description,
            scope_kind=view.role.scope_kind,
            is_builtin=view.role.is_builtin,
            require_mfa=view.role.require_mfa,
            grants=list(view.grants),
            assignment_count=view.assignments,
        )


class RoleAssignmentResponse(BaseModel):
    """역할 할당 하나.

    주체 이름을 함께 준다. id 만 주면 화면이 "누구에게 줬는지" 를 못 보여
    주고, 회수 버튼이 무엇을 회수하는지 알 수 없다.
    """

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    role_id: UUID
    scope_kind: str
    scope_id: UUID | None
    principal_kind: str
    principal_id: UUID
    #: 사람이면 이메일, 그룹이면 그룹 이름. 못 찾으면 None(지워진 주체).
    principal_label: str | None = None


class RoleAssignRequest(BaseModel):
    role_id: UUID
    scope_kind: str = Field(pattern="^(global|project|space|queue)$")
    scope_id: UUID | None = None
    principal_kind: str = Field(pattern="^(user|group)$")
    principal_id: UUID


class PermissionDefResponse(BaseModel):
    """권한 하나의 정의.

    **설명 문구를 내보내지 않는다.** `permissions.py` 의 `description` 은
    한국어로 고정된 개발자용 메모다 — 내보내면 화면이 그것을 그리고, 영어
    화면에 한국어가 섞인다(WebAuthn 자격증명 이름에서 같은 실수를 했다).
    서버는 키만 주고 표시 이름은 화면이 `admin:permission.<키>` 로 번역한다
    (i18n.md 1절). `test_i18n_permission_names.py` 가 두 언어를 고정한다.
    """

    key: str
    #: 이 권한을 붙일 수 있는 스코프. 화면이 역할 편집에서 걸러 쓴다.
    scope_kinds: list[str]
    #: 참이면 2FA 를 방금 통과한 세션만 쓸 수 있다.
    requires_step_up: bool


class SecurityPolicyResponse(BaseModel):
    """조직 전체 보안 정책. 지금은 2FA 강제 하나뿐이다."""

    require_mfa: bool


class SecurityPolicyRequest(BaseModel):
    require_mfa: bool
