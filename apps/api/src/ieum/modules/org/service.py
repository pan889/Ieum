"""org 비즈니스 로직: 프로젝트 계층과 역할 할당."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import ConflictError, NotFoundError, ValidationError
from ieum.core.pagination import Page, PageRequest
from ieum.core.permissions import PermissionService, Scope, ScopeKind
from ieum.modules.identity import contracts as identity
from ieum.modules.org import contracts
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
        self,
        actor: Actor,
        request: PageRequest,
        *,
        include_archived: bool = False,
        query: str | None = None,
    ) -> Page[Project]:
        acl = await self._perms.acl_for(self._s, actor, perms.PROJECT_VIEW)
        return await self._projects.list_page(
            request, acl=acl, include_archived=include_archived, query=query
        )

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
        require_mfa: bool = False,
    ) -> Role:
        await self._perms.require(self._s, actor, perms.ROLE_MANAGE, scope=Scope.global_())
        self._validate_grants(grants)

        if await self._roles.get_by_name(name, scope_kind) is not None:
            raise ConflictError("같은 이름·스코프의 역할이 이미 있다.")

        role = Role(
            name=name, scope_kind=scope_kind, description=description, require_mfa=require_mfa
        )
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

    async def list_roles(self, actor: Actor) -> list[RoleView]:
        """역할 목록 — 권한과 할당 수까지.

        할당 수를 함께 주는 이유: 지우기 전에 몇 명이 영향을 받는지 알아야
        한다. 화면에서 역할마다 할당을 또 부르면 목록 한 번에 N+1 이 된다.
        """
        await self._perms.require(self._s, actor, perms.ROLE_MANAGE, scope=Scope.global_())
        roles = await self._roles.list_all()
        grants = await self._roles.grants_by_role()
        counts = await self._roles.assignment_counts()
        return [
            RoleView(role=role, grants=grants.get(role.id, []), assignments=counts.get(role.id, 0))
            for role in roles
        ]

    async def update_role(
        self,
        actor: Actor,
        role_id: UUID,
        *,
        grants: list[str] | None = None,
        description: str | None = None,
        require_mfa: bool | None = None,
    ) -> Role:
        """역할의 권한·설명·2FA 요구를 고친다.

        **내장 역할의 권한은 못 고친다.** 시드가 매 기동마다 `BUILTIN_ROLES`
        정의로 되돌린다(빠진 것을 넣고 남는 것을 회수한다) — 여기서 받아 주면
        화면은 성공을 보여 주고 다음 배포가 조용히 되돌린다. 설명과 2FA
        요구는 시드가 손대지 않으므로 고칠 수 있다.

        고칠 길이 없으면 **잘못 만든 역할을 되돌릴 수 없다.** 같은 이름으로
        새로 만드는 길은 유일 제약이 막고, 이미 준 할당은 그 역할을 가리킨다.
        """
        await self._perms.require(self._s, actor, perms.ROLE_MANAGE, scope=Scope.global_())
        role = await self._require_role(role_id)

        if grants is not None:
            if role.is_builtin:
                raise ConflictError(
                    f"'{role.name}' 은 내장 역할이다. 권한 정의는 바꿀 수 없다.",
                    code="org.role_is_builtin",
                )
            self._validate_grants(grants)
            self._require_scope_fits(role.scope_kind, grants)
            # 자기 발을 쏘는 자리. 마지막 관리자가 자기 역할에서 이 권한을
            # 빼면 역할 화면 자체를 영구히 잃는다.
            await self._refuse_self_lockout(actor, role, grants)
            await self._roles.replace_grants(role.id, grants)

        if description is not None:
            role.description = description or None
        if require_mfa is not None:
            role.require_mfa = require_mfa
        await self._s.flush()
        return role

    async def delete_role(self, actor: Actor, role_id: UUID) -> None:
        """역할을 지운다. 할당도 함께 사라진다(FK CASCADE).

        내장 역할은 못 지운다 — 시드가 다음 기동에 다시 만든다.
        """
        await self._perms.require(self._s, actor, perms.ROLE_MANAGE, scope=Scope.global_())
        role = await self._require_role(role_id)
        if role.is_builtin:
            raise ConflictError(
                f"'{role.name}' 은 내장 역할이다. 지울 수 없다.",
                code="org.role_is_builtin",
            )
        await self._refuse_self_lockout(actor, role, [])
        await self._roles.delete_role(role_id)

    async def assignments(self, actor: Actor, role_id: UUID) -> list[RoleAssignment]:
        await self._perms.require(self._s, actor, perms.ROLE_MANAGE, scope=Scope.global_())
        await self._require_role(role_id)
        return await self._roles.assignments_of(role_id)

    async def revoke(self, actor: Actor, assignment_id: UUID) -> None:
        """할당을 회수한다.

        **주는 길만 있으면 그건 권한 관리가 아니다.** 잘못 준 것을 되돌릴 수
        없으면 실수 하나가 영구히 남는다.
        """
        assignment = await self._roles.assignment(assignment_id)
        if assignment is None:
            raise NotFoundError("역할 할당을 찾을 수 없다.")

        kind = ScopeKind(assignment.scope_kind)
        scope = Scope.global_() if kind is ScopeKind.GLOBAL else Scope(kind, assignment.scope_id)
        await self._perms.require(self._s, actor, perms.ROLE_ASSIGN, scope=scope)

        role = await self._require_role(assignment.role_id)
        if self._is_self(actor, assignment) and await self._grants_role_manage(role):
            raise ConflictError(
                "자기 관리자 역할은 스스로 회수할 수 없다.",
                code="org.cannot_revoke_own_admin",
            )
        await self._roles.delete_assignment(assignment_id)

    async def _require_role(self, role_id: UUID) -> Role:
        role = await self._roles.get(role_id)
        if role is None:
            raise NotFoundError("역할을 찾을 수 없다.")
        return role

    @staticmethod
    def _is_self(actor: Actor, assignment: RoleAssignment) -> bool:
        if assignment.principal_kind == "user":
            return assignment.principal_id == actor.user_id
        return assignment.principal_id in actor.group_ids

    async def _grants_role_manage(self, role: Role) -> bool:
        grants = await self._roles.grants_by_role()
        return perms.ROLE_MANAGE in grants.get(role.id, [])

    async def _refuse_self_lockout(self, actor: Actor, role: Role, grants: list[str]) -> None:
        """이 역할이 행위자의 유일한 `ROLE_MANAGE` 원천이면 손대지 못하게 한다.

        빼는 순간 역할 화면에 다시 들어갈 수 없고, 되돌려 줄 사람이 없다.
        """
        if perms.ROLE_MANAGE in grants:
            return
        if not await self._grants_role_manage(role):
            return

        mine = [a for a in await self._roles.assignments_of(role.id) if self._is_self(actor, a)]
        if not mine:
            return

        others = await self._other_role_manage_sources(actor, role.id)
        if others:
            return
        raise ConflictError(
            "이 역할이 당신의 유일한 역할 관리 권한이다. 먼저 다른 관리자를 두라.",
            code="org.cannot_drop_own_role_manage",
        )

    async def _other_role_manage_sources(self, actor: Actor, excluding: UUID) -> list[UUID]:
        """행위자에게 `ROLE_MANAGE` 를 주는 **다른** 역할들."""
        grants = await self._roles.grants_by_role()
        found: list[UUID] = []
        for role in await self._roles.list_all():
            if role.id == excluding or perms.ROLE_MANAGE not in grants.get(role.id, []):
                continue
            if any(self._is_self(actor, a) for a in await self._roles.assignments_of(role.id)):
                found.append(role.id)
        return found

    def _require_scope_fits(self, scope_kind: str, grants: list[str]) -> None:
        """역할의 스코프에서 쓸 수 없는 권한은 거절한다.

        전역 전용 권한을 프로젝트 역할에 넣으면 저장은 되고 평가에서 조용히
        무시된다 — "줬는데 안 된다" 가 된다.

        **전역 역할은 무엇이든 담을 수 있다.** 전역 할당은 모든 프로젝트·
        스페이스를 덮으므로(`OrgPermissionResolver.acl_for` 가 `is_global` 을
        세우면 어떤 스코프에서도 통과한다), 프로젝트 스코프 권한을 전역
        역할에 넣는 것은 정상이다 — 시드의 Administrator 가 바로 그 모양이다.

        한동안 이 검사가 그 방향까지 막았다. 그러면 관리자가 역할 화면에서
        전역 역할을 만들어 `issue.create` 를 넣을 수 없다 — **시드 자신이
        하는 일을 UI 로는 할 수 없는** 상태였다. 막아야 하는 것은 반대
        방향뿐이다: 좁은 역할에 전역 전용 권한.
        """
        from ieum.core.permissions import ScopeKind, registry

        if scope_kind == ScopeKind.GLOBAL.value:
            return

        bad = [
            g for g in grants if scope_kind not in {k.value for k in registry.get(g).scope_kinds}
        ]
        if bad:
            raise ValidationError(
                f"{scope_kind} 스코프 역할에 넣을 수 없는 권한이다.",
                code="org.permission_scope_mismatch",
                details={"permissions": bad, "scope_kind": scope_kind},
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


@dataclass(frozen=True, slots=True)
class RoleView:
    """역할 하나 + 화면이 필요한 곁가지."""

    role: Role
    grants: list[str]
    #: 이 역할을 받은 주체 수. 지우기 전에 영향 범위를 알아야 한다.
    assignments: int


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


class SecurityPolicyService:
    """조직 전체 보안 정책 (auth.md 3절).

    지금은 2FA 강제 하나뿐이지만, IdP 스위치와 로컬 로그인 비활성화가 이
    자리로 온다.
    """

    def __init__(self, session: AsyncSession, permissions: PermissionService) -> None:
        self._s = session
        self._perms = permissions

    async def get(self, actor: Actor) -> bool:
        await self._perms.require(self._s, actor, perms.SECURITY_MANAGE, scope=Scope.global_())
        return await contracts.workspace_requires_mfa(self._s)

    async def set_require_mfa(self, actor: Actor, *, required: bool) -> bool:
        """켜는 것과 끄는 것의 무게가 다르다.

        켤 때는 step-up 을 요구하지 않는다 — 2FA 를 켜려면 2FA 가 있어야
        한다면, 아직 아무도 안 쓰는 조직은 영영 켤 수 없다. 끌 때는
        요구한다: 보호를 푸는 일이고, 그때는 이미 모두가 2FA 를 갖고 있다.
        """
        await self._perms.require(self._s, actor, perms.SECURITY_MANAGE, scope=Scope.global_())
        current = await contracts.workspace_requires_mfa(self._s)
        if current and not required:
            self._perms.require_step_up(actor)

        value = await contracts.set_workspace_requires_mfa(self._s, required=required)
        if value != current:
            identity.record_audit(
                self._s,
                action=identity.AUDIT_SECURITY_MFA_POLICY_CHANGED,
                actor_id=actor.user_id,
                target_type="workspace",
                metadata={"require_mfa": value},
            )
        return value
