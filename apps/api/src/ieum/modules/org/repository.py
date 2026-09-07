"""org 데이터 접근과 권한 리졸버."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import Acl, Scope, ScopeKind
from ieum.modules.org.models import (
    PermissionGrant,
    Project,
    Role,
    RoleAssignment,
    Workspace,
)


class WorkspaceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get_single(self) -> Workspace | None:
        """설치당 1행. 없으면 시드가 안 돌았다는 뜻이다."""
        stmt = select(Workspace).order_by(Workspace.created_at).limit(1)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, workspace: Workspace) -> Workspace:
        self._s.add(workspace)
        return workspace


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, project_id: UUID) -> Project | None:
        return await self._s.get(Project, project_id)

    async def get_by_key(self, key: str) -> Project | None:
        stmt = select(Project).where(Project.key == key.upper())
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_many(self, project_ids: Sequence[UUID]) -> list[Project]:
        if not project_ids:
            return []
        stmt = select(Project).where(Project.id.in_(project_ids))
        return list((await self._s.execute(stmt)).scalars().all())

    def add(self, project: Project) -> Project:
        self._s.add(project)
        return project

    async def ancestor_ids(self, project_id: UUID) -> list[UUID]:
        """자신을 포함한 조상 프로젝트 ID.

        하위 프로젝트는 상위 스코프의 역할 할당을 상속하므로, 권한을 평가하려면
        조상 사슬 전체가 필요하다 (auth.md 5절).
        """
        base = (
            select(Project.id, Project.parent_id)
            .where(Project.id == project_id)
            .cte("ancestors", recursive=True)
        )
        parent = Project.__table__.alias("p")
        base = base.union_all(
            select(parent.c.id, parent.c.parent_id).join(base, base.c.parent_id == parent.c.id)
        )
        return list((await self._s.execute(select(base.c.id))).scalars().all())

    async def descendant_ids(self, project_id: UUID) -> list[UUID]:
        """자신을 포함한 하위 프로젝트 ID. 상위 할당을 하위로 펼칠 때 쓴다."""
        base = select(Project.id).where(Project.id == project_id).cte("descendants", recursive=True)
        child = Project.__table__.alias("c")
        base = base.union_all(select(child.c.id).join(base, child.c.parent_id == base.c.id))
        return list((await self._s.execute(select(base.c.id))).scalars().all())

    async def list_page(
        self,
        request: PageRequest,
        *,
        acl: Acl,
        include_archived: bool = False,
        query: str | None = None,
    ) -> Page[Project]:
        """목록은 검사하지 않고 필터링한다 (auth.md 5절)."""
        if acl.is_empty:
            return Page(items=[])

        stmt: Select[tuple[Project]] = select(Project)
        if not acl.is_global:
            stmt = stmt.where(Project.id.in_(acl.project_ids))
        if not include_archived:
            stmt = stmt.where(Project.archived_at.is_(None))
        if query:
            # 키로도 이름으로도 찾는다. 사람은 둘 중 기억나는 쪽을 친다.
            like = f"%{query.strip().lower()}%"
            stmt = stmt.where(
                func.lower(Project.key).like(like) | func.lower(Project.name).like(like)
            )

        payload = request.cursor_payload
        if payload:
            stmt = stmt.where(Project.key > payload["key"])
        stmt = stmt.order_by(Project.key).limit(request.fetch_limit)

        rows = list((await self._s.execute(stmt)).scalars().all())
        return Page.from_rows(rows, request, lambda p: {"key": p.key})


class RoleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, role_id: UUID) -> Role | None:
        return await self._s.get(Role, role_id)

    async def get_by_name(self, name: str, scope_kind: str) -> Role | None:
        stmt = select(Role).where(Role.name == name, Role.scope_kind == scope_kind)
        return (await self._s.execute(stmt)).scalar_one_or_none()

    def add(self, role: Role) -> Role:
        self._s.add(role)
        return role

    def grant(self, role_id: UUID, permission: str) -> PermissionGrant:
        row = PermissionGrant(role_id=role_id, permission=permission)
        self._s.add(row)
        return row

    def assign(
        self,
        *,
        role_id: UUID,
        scope: Scope,
        principal_kind: str,
        principal_id: UUID,
    ) -> RoleAssignment:
        row = RoleAssignment(
            role_id=role_id,
            scope_kind=scope.kind.value,
            scope_id=scope.id,
            principal_kind=principal_kind,
            principal_id=principal_id,
        )
        self._s.add(row)
        return row

    async def list_all(self) -> list[Role]:
        return list((await self._s.execute(select(Role).order_by(Role.name))).scalars().all())

    async def grants_by_role(self) -> dict[UUID, list[str]]:
        """역할 → 권한 목록. 역할마다 따로 물으면 목록 한 번에 N+1 이 된다."""
        rows = (
            await self._s.execute(
                select(PermissionGrant.role_id, PermissionGrant.permission).order_by(
                    PermissionGrant.permission
                )
            )
        ).all()
        grants: dict[UUID, list[str]] = {}
        for role_id, permission in rows:
            grants.setdefault(role_id, []).append(permission)
        return grants

    async def assignment_counts(self) -> dict[UUID, int]:
        rows = (
            await self._s.execute(
                select(RoleAssignment.role_id, func.count(RoleAssignment.id)).group_by(
                    RoleAssignment.role_id
                )
            )
        ).all()
        return {row[0]: row[1] for row in rows}

    async def assignments_of(self, role_id: UUID) -> list[RoleAssignment]:
        stmt = (
            select(RoleAssignment)
            .where(RoleAssignment.role_id == role_id)
            .order_by(RoleAssignment.created_at, RoleAssignment.id)
        )
        return list((await self._s.execute(stmt)).scalars().all())

    async def assignment(self, assignment_id: UUID) -> RoleAssignment | None:
        return await self._s.get(RoleAssignment, assignment_id)

    async def replace_grants(self, role_id: UUID, permissions: Sequence[str]) -> None:
        """권한 목록을 통째로 갈아 끼운다.

        차이만 적용하지 않는다. 목록 하나를 정본으로 두면 "무엇이 남아 있나"
        를 따로 추론할 필요가 없다 — 남은 권한 하나가 조용히 사는 쪽이 훨씬
        나쁜 실패다.
        """
        await self._s.execute(delete(PermissionGrant).where(PermissionGrant.role_id == role_id))
        for permission in dict.fromkeys(permissions):
            self._s.add(PermissionGrant(role_id=role_id, permission=permission))

    async def delete_role(self, role_id: UUID) -> None:
        """역할을 지운다. 권한과 할당은 FK 의 ON DELETE CASCADE 가 정리한다."""
        await self._s.execute(delete(Role).where(Role.id == role_id))

    async def delete_assignment(self, assignment_id: UUID) -> None:
        await self._s.execute(delete(RoleAssignment).where(RoleAssignment.id == assignment_id))


class OrgPermissionResolver:
    """core.PermissionResolver 구현체.

    core 는 이 클래스를 타입으로만 알고, 인스턴스는 기동 시 주입된다.
    그래서 의존 방향이 org → core 한쪽으로 유지된다.
    """

    def __init__(self) -> None:
        self._projects: ProjectRepository | None = None

    async def permissions_in_scope(
        self, session: AsyncSession, actor: Actor, scope: Scope
    ) -> frozenset[str]:
        principals = list(actor.principal_ids)
        if not principals:
            return frozenset()

        # 전역 할당은 모든 스코프에 적용된다.
        conditions = [
            (RoleAssignment.scope_kind == ScopeKind.GLOBAL.value)
            & RoleAssignment.scope_id.is_(None)
        ]

        if scope.kind is ScopeKind.PROJECT and scope.id is not None:
            # 상위 프로젝트의 할당을 상속한다.
            ancestors = await ProjectRepository(session).ancestor_ids(scope.id)
            conditions.append(
                (RoleAssignment.scope_kind == ScopeKind.PROJECT.value)
                & RoleAssignment.scope_id.in_(ancestors or [scope.id])
            )
        elif scope.kind is not ScopeKind.GLOBAL and scope.id is not None:
            conditions.append(
                (RoleAssignment.scope_kind == scope.kind.value)
                & (RoleAssignment.scope_id == scope.id)
            )

        scope_filter = conditions[0]
        for extra in conditions[1:]:
            scope_filter = scope_filter | extra

        stmt = (
            select(PermissionGrant.permission)
            .join(RoleAssignment, RoleAssignment.role_id == PermissionGrant.role_id)
            .where(RoleAssignment.principal_id.in_(principals))
            .where(scope_filter)
            .distinct()
        )
        return frozenset((await session.execute(stmt)).scalars().all())

    async def acl_for(self, session: AsyncSession, actor: Actor, permission: str) -> Acl:
        principals = list(actor.principal_ids)
        if not principals:
            return Acl(permission=permission)

        stmt = (
            select(RoleAssignment.scope_kind, RoleAssignment.scope_id)
            .join(PermissionGrant, PermissionGrant.role_id == RoleAssignment.role_id)
            .where(PermissionGrant.permission == permission)
            .where(RoleAssignment.principal_id.in_(principals))
            .distinct()
        )
        rows = list((await session.execute(stmt)).all())

        is_global = any(kind == ScopeKind.GLOBAL.value for kind, _ in rows)
        if is_global:
            return Acl(permission=permission, is_global=True)

        direct_projects = {sid for kind, sid in rows if kind == ScopeKind.PROJECT.value and sid}
        # 상위 프로젝트 할당은 하위에도 유효하므로 자손까지 펼친다.
        projects: set[UUID] = set()
        repo = ProjectRepository(session)
        for pid in direct_projects:
            projects.update(await repo.descendant_ids(pid))

        return Acl(
            permission=permission,
            project_ids=frozenset(projects),
            space_ids=frozenset(sid for kind, sid in rows if kind == ScopeKind.SPACE.value and sid),
            queue_ids=frozenset(sid for kind, sid in rows if kind == ScopeKind.QUEUE.value and sid),
        )


__all__ = [
    "OrgPermissionResolver",
    "ProjectRepository",
    "RoleRepository",
    "WorkspaceRepository",
]
