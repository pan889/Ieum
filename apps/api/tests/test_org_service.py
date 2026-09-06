"""org 서비스 레이어 테스트.

프로젝트 계층과 권한 상속이 핵심이다 — 여기가 틀리면 "보이면 안 되는 게
보이는" 사고가 난다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import GroupMember, User, UserGroup
from ieum.modules.org import permissions as perms
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository
from ieum.modules.org.service import ProjectService, RoleService, WorkspaceService


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> AsyncIterator[User]:
    row = User(email=f"u-{new_id()}@example.com", display_name="Tester", status="active")
    session.add(row)
    await session.flush()
    yield row


def actor_for(user: User, *, group_ids: frozenset[UUID] = frozenset()) -> Actor:
    """step-up 이 필요한 권한도 쓰므로 MFA 를 통과한 액터로 만든다."""
    return Actor(
        user_id=user.id,
        email=user.email,
        is_active=True,
        mfa_satisfied_at=utcnow(),
        group_ids=group_ids,
    )


async def grant(
    session: AsyncSession,
    *,
    principal_id: UUID,
    permissions_granted: tuple[str, ...],
    scope: Scope,
    principal_kind: str = "user",
    scope_kind: str | None = None,
) -> Role:
    """역할을 만들고 주체에게 할당한다."""
    repo = RoleRepository(session)
    role = Role(name=f"role-{new_id()}", scope_kind=scope_kind or scope.kind.value)
    repo.add(role)
    await session.flush()
    for permission in permissions_granted:
        repo.grant(role.id, permission)
    repo.assign(
        role_id=role.id, scope=scope, principal_kind=principal_kind, principal_id=principal_id
    )
    await session.flush()
    return role


class TestWorkspace:
    async def test_ensure_is_idempotent(self, session: AsyncSession) -> None:
        service = WorkspaceService(session)
        first = await service.ensure(name="Ieum")
        second = await service.ensure(name="다른 이름")
        assert first.id == second.id
        assert second.name == "Ieum"  # 이미 있으면 덮어쓰지 않는다


class TestProjectCreation:
    async def test_requires_global_create_permission(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        service = ProjectService(session, permissions)
        with pytest.raises(PermissionDeniedError):
            await service.create(actor_for(user), key="ENG", name="Engineering")

    async def test_creates_with_permission(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_CREATE,),
            scope=Scope.global_(),
        )
        project = await ProjectService(session, permissions).create(
            actor_for(user), key="eng", name="  Engineering  "
        )
        assert project.key == "ENG"  # 대문자로 정규화
        assert project.name == "Engineering"  # 공백 제거

    @pytest.mark.parametrize("bad_key", ["e", "1ENG", "eng-1", "TOOOOOOOOOOOOOOOOOLONG", ""])
    async def test_rejects_invalid_keys(
        self, session: AsyncSession, user: User, permissions: PermissionService, bad_key: str
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_CREATE,),
            scope=Scope.global_(),
        )
        with pytest.raises(ValidationError) as exc:
            await ProjectService(session, permissions).create(
                actor_for(user), key=bad_key, name="X"
            )
        assert exc.value.code == "org.invalid_project_key"

    async def test_rejects_duplicate_key(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_CREATE,),
            scope=Scope.global_(),
        )
        service = ProjectService(session, permissions)
        await service.create(actor_for(user), key="ENG", name="First")
        with pytest.raises(ConflictError) as exc:
            await service.create(actor_for(user), key="eng", name="Second")
        assert exc.value.code == "org.project_key_taken"

    async def test_subproject_needs_parent_admin_not_global_create(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """하위 프로젝트는 상위의 관리 권한으로 만든다. 전역 생성 권한이 없어도 된다."""
        creator = User(email=f"a-{new_id()}@e.com", display_name="A", status="active")
        session.add(creator)
        await session.flush()
        await grant(
            session,
            principal_id=creator.id,
            permissions_granted=(perms.PROJECT_CREATE,),
            scope=Scope.global_(),
        )
        service = ProjectService(session, permissions)
        parent = await service.create(actor_for(creator), key="ENG", name="Engineering")

        # user 는 전역 생성 권한이 없고 부모의 admin 만 있다.
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_ADMIN,),
            scope=Scope.project(parent.id),
        )
        child = await service.create(actor_for(user), key="ENGAPI", name="API", parent_id=parent.id)
        assert child.parent_id == parent.id

    async def test_depth_limit(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_CREATE, perms.PROJECT_ADMIN),
            scope=Scope.global_(),
            scope_kind="global",
        )
        service = ProjectService(session, permissions)
        actor = actor_for(user)

        parent_id: UUID | None = None
        for depth in range(5):
            created = await service.create(
                actor, key=f"LVL{depth}", name=f"Level {depth}", parent_id=parent_id
            )
            parent_id = created.id

        with pytest.raises(ValidationError) as exc:
            await service.create(actor, key="LVL5", name="Too deep", parent_id=parent_id)
        assert exc.value.code == "org.project_too_deep"

    async def test_unknown_parent(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_ADMIN,),
            scope=Scope.global_(),
            scope_kind="global",
        )
        with pytest.raises(NotFoundError):
            await ProjectService(session, permissions).create(
                actor_for(user), key="ORPH", name="Orphan", parent_id=new_id()
            )


class TestPermissionInheritance:
    async def test_child_inherits_parent_assignment(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """상위 프로젝트의 역할 할당이 하위에도 적용된다 (auth.md 5절)."""
        parent = Project(key="ENG", name="Engineering")
        session.add(parent)
        await session.flush()
        child = Project(key="ENGAPI", name="API", parent_id=parent.id)
        session.add(child)
        await session.flush()

        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.project(parent.id),
        )

        actor = actor_for(user)
        assert await permissions.has(
            session, actor, perms.PROJECT_VIEW, scope=Scope.project(child.id)
        )

    async def test_parent_does_not_inherit_from_child(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """상속은 아래로만 흐른다. 하위 권한이 상위를 열어주면 안 된다."""
        parent = Project(key="ENG", name="Engineering")
        session.add(parent)
        await session.flush()
        child = Project(key="ENGAPI", name="API", parent_id=parent.id)
        session.add(child)
        await session.flush()

        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.project(child.id),
        )

        actor = actor_for(user)
        assert not await permissions.has(
            session, actor, perms.PROJECT_VIEW, scope=Scope.project(parent.id)
        )

    async def test_group_membership_grants_permission(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        group = UserGroup(name=f"devs-{new_id()}")
        session.add(group)
        await session.flush()
        session.add(GroupMember(group_id=group.id, user_id=user.id))
        await session.flush()

        project = Project(key="ENG", name="Engineering")
        session.add(project)
        await session.flush()

        await grant(
            session,
            principal_id=group.id,
            principal_kind="group",
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.project(project.id),
        )

        actor = actor_for(user, group_ids=frozenset({group.id}))
        assert await permissions.has(
            session, actor, perms.PROJECT_VIEW, scope=Scope.project(project.id)
        )

    async def test_acl_expands_to_descendants(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """목록 필터용 ACL 은 상위 할당을 자손까지 펼쳐야 한다."""
        parent = Project(key="ENG", name="Engineering")
        session.add(parent)
        await session.flush()
        child = Project(key="ENGAPI", name="API", parent_id=parent.id)
        other = Project(key="OPS", name="Operations")
        session.add_all([child, other])
        await session.flush()

        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.project(parent.id),
        )

        acl = await permissions.acl_for(session, actor_for(user), perms.PROJECT_VIEW)
        assert parent.id in acl.project_ids
        assert child.id in acl.project_ids
        assert other.id not in acl.project_ids


class TestProjectListing:
    async def test_lists_only_visible_projects(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        visible = Project(key="SEEN", name="Visible")
        hidden = Project(key="HIDDEN", name="Hidden")
        session.add_all([visible, hidden])
        await session.flush()

        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.project(visible.id),
        )

        page = await ProjectService(session, permissions).list_for(
            actor_for(user), PageRequest(limit=50)
        )
        assert [p.key for p in page.items] == ["SEEN"]

    async def test_empty_acl_returns_nothing(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        session.add(Project(key="SECRET", name="Secret"))
        await session.flush()
        page = await ProjectService(session, permissions).list_for(actor_for(user), PageRequest())
        assert page.items == []

    async def test_archived_excluded_by_default(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        live = Project(key="LIVE", name="Live")
        gone = Project(key="GONE", name="Gone", archived_at=utcnow())
        session.add_all([live, gone])
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.global_(),
            scope_kind="global",
        )
        actor = actor_for(user)
        service = ProjectService(session, permissions)

        default_page = await service.list_for(actor, PageRequest())
        assert {p.key for p in default_page.items} == {"LIVE"}

        with_archived = await service.list_for(actor, PageRequest(), include_archived=True)
        assert {p.key for p in with_archived.items} == {"LIVE", "GONE"}

    async def test_cursor_paginates_without_gaps(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        for i in range(5):
            session.add(Project(key=f"P{i}", name=f"Project {i}"))
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_VIEW,),
            scope=Scope.global_(),
            scope_kind="global",
        )
        actor = actor_for(user)
        service = ProjectService(session, permissions)

        collected: list[str] = []
        cursor: str | None = None
        for _ in range(5):
            page = await service.list_for(actor, PageRequest(limit=2, cursor=cursor))
            collected.extend(p.key for p in page.items)
            cursor = page.next_cursor
            if cursor is None:
                break

        assert collected == ["P0", "P1", "P2", "P3", "P4"]


class TestProjectArchive:
    async def test_requires_permission(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        project = Project(key="ENG", name="Engineering")
        session.add(project)
        await session.flush()
        with pytest.raises(PermissionDeniedError):
            await ProjectService(session, permissions).archive(actor_for(user), project.id)

    async def test_archive_is_idempotent(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        project = Project(key="ENG", name="Engineering")
        session.add(project)
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_ARCHIVE,),
            scope=Scope.project(project.id),
        )
        service = ProjectService(session, permissions)
        first = await service.archive(actor_for(user), project.id)
        stamp = first.archived_at
        second = await service.archive(actor_for(user), project.id)
        assert second.archived_at == stamp

    async def test_blocked_while_children_are_live(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """하위가 살아 있는데 상위를 접으면 고아가 생긴다."""
        parent = Project(key="ENG", name="Engineering")
        session.add(parent)
        await session.flush()
        session.add(Project(key="ENGAPI", name="API", parent_id=parent.id))
        await session.flush()

        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PROJECT_ARCHIVE,),
            scope=Scope.project(parent.id),
        )
        with pytest.raises(ConflictError) as exc:
            await ProjectService(session, permissions).archive(actor_for(user), parent.id)
        assert exc.value.code == "org.project_has_active_children"
        assert exc.value.details["child_key"] == "ENGAPI"

    async def test_unknown_project(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        with pytest.raises(NotFoundError):
            await ProjectService(session, permissions).archive(actor_for(user), new_id())


class TestRoleService:
    async def test_rejects_unknown_permission(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """오타 난 권한이 역할에 들어가면 조용히 아무것도 못 하는 역할이 된다."""
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.ROLE_MANAGE,),
            scope=Scope.global_(),
        )
        with pytest.raises(ValidationError) as exc:
            await RoleService(session, permissions).create_role(
                actor_for(user),
                name="Typo Role",
                scope_kind="global",
                grants=["org.projekt.view"],
            )
        assert exc.value.code == "org.unknown_permission"
        assert exc.value.details["unknown"] == ["org.projekt.view"]

    async def test_creates_role_with_grants(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.ROLE_MANAGE,),
            scope=Scope.global_(),
        )
        role = await RoleService(session, permissions).create_role(
            actor_for(user),
            name=f"Viewer-{new_id()}",
            scope_kind="project",
            grants=[perms.PROJECT_VIEW],
        )
        assert role.scope_kind == "project"

    async def test_scope_mismatch_rejected(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        """프로젝트 역할을 전역에 할당하면 권한이 의도보다 넓어진다."""
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.ROLE_MANAGE, perms.ROLE_ASSIGN),
            scope=Scope.global_(),
        )
        service = RoleService(session, permissions)
        role = await service.create_role(
            actor_for(user),
            name=f"ProjectOnly-{new_id()}",
            scope_kind="project",
            grants=[perms.PROJECT_VIEW],
        )
        with pytest.raises(ValidationError) as exc:
            await service.assign(
                actor_for(user),
                role_id=role.id,
                scope=Scope.global_(),
                principal_kind="user",
                principal_id=user.id,
            )
        assert exc.value.code == "org.role_scope_mismatch"

    async def test_assign_unknown_role(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.ROLE_ASSIGN,),
            scope=Scope.global_(),
        )
        with pytest.raises(NotFoundError):
            await RoleService(session, permissions).assign(
                actor_for(user),
                role_id=new_id(),
                scope=Scope.global_(),
                principal_kind="user",
                principal_id=user.id,
            )
