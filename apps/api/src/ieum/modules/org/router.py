"""org HTTP 라우터."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.core.permissions import Scope, ScopeKind, registry
from ieum.modules.identity import contracts as identity
from ieum.modules.org.schemas import (
    PermissionDefResponse,
    ProjectCreateRequest,
    ProjectPageResponse,
    ProjectResponse,
    RoleAssignmentResponse,
    RoleAssignRequest,
    RoleCreateRequest,
    RoleDetailResponse,
    RoleResponse,
    RoleUpdateRequest,
    SecurityPolicyRequest,
    SecurityPolicyResponse,
)
from ieum.modules.org.service import ProjectService, RoleService, SecurityPolicyService

projects_router = APIRouter(prefix="/projects", tags=["projects"])
roles_router = APIRouter(prefix="/roles", tags=["roles"])
security_router = APIRouter(prefix="/admin/security", tags=["security"])


@projects_router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> ProjectResponse:
    project = await ProjectService(session, permissions).create(
        actor,
        key=body.key,
        name=body.name,
        description=body.description,
        parent_id=body.parent_id,
        lead_id=body.lead_id,
        is_public=body.is_public,
    )
    await session.commit()
    return ProjectResponse.model_validate(project)


@projects_router.get("", response_model=ProjectPageResponse)
async def list_projects(
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
    include_archived: bool = False,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> ProjectPageResponse:
    """보이는 것만 돌려준다. 권한 없는 프로젝트는 SQL 단계에서 걸러진다."""
    page = await ProjectService(session, permissions).list_for(
        actor,
        PageRequest(limit=limit, cursor=cursor),
        include_archived=include_archived,
        query=q,
    )
    return ProjectPageResponse(
        items=[ProjectResponse.model_validate(p) for p in page.items],
        next_cursor=page.next_cursor,
        total=page.total,
    )


@projects_router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> ProjectResponse:
    project = await ProjectService(session, permissions).get(actor, project_id)
    return ProjectResponse.model_validate(project)


@projects_router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> ProjectResponse:
    project = await ProjectService(session, permissions).archive(actor, project_id)
    await session.commit()
    return ProjectResponse.model_validate(project)


@roles_router.get("/permissions", response_model=list[PermissionDefResponse])
async def list_permissions(actor: CurrentActor) -> list[PermissionDefResponse]:
    """등록된 권한 상수 전부. 관리 UI 가 역할 편집 화면을 그릴 때 쓴다."""
    return [
        PermissionDefResponse(
            key=d.key,
            scope_kinds=sorted(k.value for k in d.scope_kinds),
            requires_step_up=d.requires_step_up,
        )
        for d in registry.all()
    ]


@roles_router.post("", response_model=RoleResponse, status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> RoleResponse:
    role = await RoleService(session, permissions).create_role(
        actor,
        name=body.name,
        scope_kind=body.scope_kind,
        grants=body.grants,
        description=body.description,
        require_mfa=body.require_mfa,
    )
    await session.commit()
    return RoleResponse.model_validate(role)


@roles_router.get("", response_model=list[RoleDetailResponse])
async def list_roles(
    actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> list[RoleDetailResponse]:
    """역할 정의 전부. **내장 역할도 준다** — 무엇이 있는지 못 보면 새로
    만들 때 겹치고, 유일 제약이 그 등록을 막는다."""
    views = await RoleService(session, permissions).list_roles(actor)
    return [RoleDetailResponse.of(view) for view in views]


@roles_router.patch("/{role_id}", response_model=RoleResponse)
async def update_role(
    role_id: UUID,
    body: RoleUpdateRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> RoleResponse:
    role = await RoleService(session, permissions).update_role(
        actor,
        role_id,
        grants=body.grants,
        description=body.description,
        require_mfa=body.require_mfa,
    )
    await session.commit()
    return RoleResponse.model_validate(role)


@roles_router.delete("/{role_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_role(
    role_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    await RoleService(session, permissions).delete_role(actor, role_id)
    await session.commit()


@roles_router.get("/{role_id}/assignments", response_model=list[RoleAssignmentResponse])
async def list_role_assignments(
    role_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> list[RoleAssignmentResponse]:
    rows = await RoleService(session, permissions).assignments(actor, role_id)
    return [
        RoleAssignmentResponse(
            id=row.id,
            role_id=row.role_id,
            scope_kind=row.scope_kind,
            scope_id=row.scope_id,
            principal_kind=row.principal_kind,
            principal_id=row.principal_id,
            principal_label=await _principal_label(session, row.principal_kind, row.principal_id),
        )
        for row in rows
    ]


async def _principal_label(session: AsyncSession, kind: str, principal_id: UUID) -> str | None:
    """주체를 사람이 읽을 이름으로.

    `principal_id` 는 FK 가 아니다(사람일 수도 그룹일 수도 있다). 그래서
    지워진 주체를 가리키는 행이 있을 수 있고, 그때는 이름이 없다 — 감추지
    않는다. 화면이 id 를 보여 주고, 회수할 수 있어야 한다.
    """
    if kind == "group":
        group = await identity.get_group(session, principal_id)
        return group.name if group else None
    user = await identity.get_user(session, principal_id)
    return user.email if user else None


@roles_router.delete("/assignments/{assignment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_assignment(
    assignment_id: UUID,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    """할당을 회수한다. **주는 길만 있으면 권한 관리가 아니다.**

    `/{role_id}` 보다 **뒤에** 선언돼 있어야 한다 — 아니라도 `assignments`
    가 UUID 로 파싱되지 않아 422 가 되지만, 순서를 지키는 편이 읽기 쉽다.
    """
    await RoleService(session, permissions).revoke(actor, assignment_id)
    await session.commit()


@roles_router.post("/assignments", status_code=status.HTTP_204_NO_CONTENT)
async def assign_role(
    body: RoleAssignRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> None:
    kind = ScopeKind(body.scope_kind)
    scope = Scope.global_() if kind is ScopeKind.GLOBAL else Scope(kind, body.scope_id)
    await RoleService(session, permissions).assign(
        actor,
        role_id=body.role_id,
        scope=scope,
        principal_kind=body.principal_kind,
        principal_id=body.principal_id,
    )
    await session.commit()


@security_router.get("", response_model=SecurityPolicyResponse)
async def get_security_policy(
    actor: CurrentActor, session: DbSession, permissions: PermissionDep
) -> SecurityPolicyResponse:
    required = await SecurityPolicyService(session, permissions).get(actor)
    return SecurityPolicyResponse(require_mfa=required)


@security_router.put("", response_model=SecurityPolicyResponse)
async def set_security_policy(
    body: SecurityPolicyRequest,
    actor: CurrentActor,
    session: DbSession,
    permissions: PermissionDep,
) -> SecurityPolicyResponse:
    """조직 전체 2FA 강제. 끄는 쪽만 step-up 을 요구한다 (서비스 참고)."""
    required = await SecurityPolicyService(session, permissions).set_require_mfa(
        actor, required=body.require_mfa
    )
    await session.commit()
    return SecurityPolicyResponse(require_mfa=required)
