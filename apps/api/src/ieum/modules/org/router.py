"""org HTTP 라우터."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, status

from ieum.core.deps import CurrentActor, DbSession, PermissionDep
from ieum.core.pagination import DEFAULT_LIMIT, MAX_LIMIT, PageRequest
from ieum.core.permissions import Scope, ScopeKind, registry
from ieum.modules.org.schemas import (
    PermissionDefResponse,
    ProjectCreateRequest,
    ProjectPageResponse,
    ProjectResponse,
    RoleAssignRequest,
    RoleCreateRequest,
    RoleResponse,
)
from ieum.modules.org.service import ProjectService, RoleService

projects_router = APIRouter(prefix="/projects", tags=["projects"])
roles_router = APIRouter(prefix="/roles", tags=["roles"])


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
) -> ProjectPageResponse:
    """보이는 것만 돌려준다. 권한 없는 프로젝트는 SQL 단계에서 걸러진다."""
    page = await ProjectService(session, permissions).list_for(
        actor, PageRequest(limit=limit, cursor=cursor), include_archived=include_archived
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
            description=d.description,
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
    )
    await session.commit()
    return RoleResponse.model_validate(role)


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
