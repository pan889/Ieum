"""org 비즈니스 로직: 프로젝트 계층과 역할 할당."""

from __future__ import annotations

import re
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.org import permissions as perms
from ieum.modules.org.models import Project, Role, RoleAssignment, Workspace
from ieum.modules.org.repository import (
    ProjectRepository,
    RoleRepository,
    WorkspaceRepository,
)

PROJECT_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,15}$")
MAX_PROJECT_DEPTH = 5


class ProjectService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._projects = ProjectRepository(session)

    async def create(
        self,
        actor: Actor,
        *,
        key: str,
        name: str,
        description: str | None = None,
        parent_id: UUID | None = None,
        lead_id: UUID | None = None,
        is_public: bool = False,
    ) -> Project:
        # 하위 프로젝트를 만들려면 상위 프로젝트의 관리 권한이 있어야 한다.
        if parent_id is not None:
            await self._perms.require(
                self._s, actor, perms.PROJECT_ADMIN, scope=Scope.project(parent_id)
            )
        else:
            await self._perms.require(self._s, actor, perms.PROJECT_CREATE, scope=Scope.global_())

        normalized = key.strip().upper()
        self._validate_key(normalized)

        if await self._projects.get_by_key(normalized) is not None:
            raise ConflictError(
                f"이미 사용 중인 프로젝트 키다: {normalized}", code="org.project_key_taken"
            )

        if parent_id is not None:
            await self._validate_parent(parent_id)

        project = Project(
            key=normalized,
            name=name.strip(),
            description=description,
            parent_id=parent_id,
            lead_id=lead_id,
            is_public=is_public,
        )
        self._projects.add(project)
        await self._s.flush()
        return project

    async def get(self, actor: Actor, project_id: UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        await self._perms.require(
            self._s, actor, perms.PROJECT_VIEW, scope=Scope.project(project_id)
        )
        return project

    async def list_for(
        self, actor: Actor, request: PageRequest, *, include_archived: bool = False
    ) -> Page[Project]:
        acl = await self._perms.acl_for(self._s, actor, perms.PROJECT_VIEW)
        return await self._projects.list_page(request, acl=acl, include_archived=include_archived)

    async def archive(self, actor: Actor, project_id: UUID) -> Project:
        project = await self._projects.get(project_id)
        if project is None:
            raise NotFoundError("프로젝트를 찾을 수 없다.")
        await self._perms.require(
            self._s, actor, perms.PROJECT_ARCHIVE, scope=Scope.project(project_id)
        )
        if project.is_archived:
            return project

        # 하위가 살아 있는데 상위를 아카이브하면 고아가 생긴다.
        descendants = await self._projects.descendant_ids(project_id)
        for other_id in descendants:
            if other_id == project_id:
                continue
            child = await self._projects.get(other_id)
            if child is not None and not child.is_archived:
                raise ConflictError(
                    "하위 프로젝트를 먼저 아카이브해야 한다.",
                    code="org.project_has_active_children",
                    details={"child_key": child.key},
                )

        from ieum.core.time import utcnow

        project.archived_at = utcnow()
        return project

    def _validate_key(self, key: str) -> None:
        if not PROJECT_KEY_PATTERN.match(key):
            raise ValidationError(
                "프로젝트 키는 대문자로 시작하는 2~16자 영숫자여야 한다.",
                code="org.invalid_project_key",
                details={"key": key},
            )

    async def _validate_parent(self, parent_id: UUID) -> None:
        parent = await self._projects.get(parent_id)
        if parent is None:
            raise NotFoundError("상위 프로젝트를 찾을 수 없다.")
        if parent.is_archived:
            raise ConflictError("아카이브된 프로젝트 아래에는 만들 수 없다.")
        depth = len(await self._projects.ancestor_ids(parent_id))
        if depth >= MAX_PROJECT_DEPTH:
            raise ValidationError(
                f"프로젝트 계층은 {MAX_PROJECT_DEPTH}단계까지다.",
                code="org.project_too_deep",
                details={"max_depth": MAX_PROJECT_DEPTH},
            )


class RoleService:
    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions
        self._roles = RoleRepository(session)

    async def create_role(
        self,
        actor: Actor,
        *,
        name: str,
        scope_kind: str,
        grants: list[str],
        description: str | None = None,
    ) -> Role:
        await self._perms.require(self._s, actor, perms.ROLE_MANAGE, scope=Scope.global_())
        self._validate_grants(grants)

        if await self._roles.get_by_name(name, scope_kind) is not None:
            raise ConflictError("같은 이름·스코프의 역할이 이미 있다.")

        role = Role(name=name, scope_kind=scope_kind, description=description)
        self._roles.add(role)
        await self._s.flush()
        for permission in grants:
            self._roles.grant(role.id, permission)
        return role

    async def assign(
        self,
        actor: Actor,
        *,
        role_id: UUID,
        scope: Scope,
        principal_kind: str,
        principal_id: UUID,
    ) -> RoleAssignment:
        await self._perms.require(self._s, actor, perms.ROLE_ASSIGN, scope=scope)
        role = await self._roles.get(role_id)
        if role is None:
            raise NotFoundError("역할을 찾을 수 없다.")
        if role.scope_kind != scope.kind.value:
            raise ValidationError(
                f"'{role.name}' 은 {role.scope_kind} 스코프 역할이다.",
                code="org.role_scope_mismatch",
            )
        return self._roles.assign(
            role_id=role_id,
            scope=scope,
            principal_kind=principal_kind,
            principal_id=principal_id,
        )

    def _validate_grants(self, grants: list[str]) -> None:
        from ieum.core.permissions import registry

        unknown = [g for g in grants if g not in registry]
        if unknown:
            raise ValidationError(
                "등록되지 않은 권한이 포함됐다.",
                code="org.unknown_permission",
                details={"unknown": unknown},
            )


class WorkspaceService:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session
        self._repo = WorkspaceRepository(session)

    async def ensure(self, *, name: str = "Ieum") -> Workspace:
        """설치당 1행을 보장한다. 시드와 기동 시 호출한다."""
        existing = await self._repo.get_single()
        if existing is not None:
            return existing
        workspace = Workspace(name=name)
        self._repo.add(workspace)
        await self._s.flush()
        return workspace
