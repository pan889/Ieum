"""역할 관리.

역할은 만들고 줄 수만 있었다 — 목록도, 수정도, 회수도 없었다. 그건
**일방통행**이다: 잘못 만든 역할은 같은 이름으로 다시 만들 수 없고(유일
제약), 잘못 준 할당은 영구히 남는다. 이 제품에서 반복해 부딪힌 종류다
(IdP 끄기, SAML 인증서 회전, 그룹 멤버).

동시에 되돌릴 수 **없어야** 하는 자리도 있다: 마지막 관리자가 자기 역할에서
역할 관리 권한을 빼면 그 조직은 역할 화면을 영구히 잃는다.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.identity.models import User
from ieum.modules.org import permissions as perms
from ieum.modules.org.models import PermissionGrant, Role, RoleAssignment
from ieum.modules.org.repository import OrgPermissionResolver
from ieum.modules.org.service import RoleService
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


async def _person(session: AsyncSession) -> User:
    row = User(email=f"p-{new_id()}@example.com", display_name="Person", status="active")
    session.add(row)
    await session.flush()
    return row


async def _admin(session: AsyncSession) -> tuple[User, Role]:
    """역할을 관리·할당할 수 있는 사람. 그 권한을 **역할로** 받는다 —
    자기 잠금 방어가 그 할당을 보고 판단하기 때문이다."""
    person = await _person(session)
    role = await grant(
        session,
        principal_id=person.id,
        permissions_granted=(perms.ROLE_MANAGE, perms.ROLE_ASSIGN),
        scope=Scope.global_(),
    )
    return person, role


class TestListing:
    async def test_a_stranger_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await RoleService(session, permissions).list_roles(actor_for(nobody))

    async def test_roles_come_with_grants_and_assignment_counts(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """할당 수를 함께 준다. 지우기 전에 몇 명이 영향을 받는지 알아야
        하고, 역할마다 할당을 또 부르면 목록 한 번에 N+1 이 된다."""
        person, role = await _admin(session)
        views = {
            v.role.id: v
            for v in await RoleService(session, permissions).list_roles(actor_for(person))
        }

        mine = views[role.id]
        assert sorted(mine.grants) == sorted([perms.ROLE_MANAGE, perms.ROLE_ASSIGN])
        assert mine.assignments == 1


class TestBuiltinRoles:
    """시드가 매 기동마다 `BUILTIN_ROLES` 정의로 되돌린다 — 빠진 것을 넣고
    남는 것을 회수한다. 손으로 고친 권한은 다음 배포에 사라진다.

    **되돌려질 변경을 받아 주면 화면은 성공을 보여 주고 결과는 없어진다.**
    그래서 거절한다(IdP 가 관리하는 그룹과 같은 판단).
    """

    async def _builtin(self, session: AsyncSession) -> Role:
        role = Role(name=f"Builtin {new_id()}", scope_kind="global", is_builtin=True)
        session.add(role)
        await session.flush()
        session.add(PermissionGrant(role_id=role.id, permission=perms.PROJECT_VIEW))
        await session.flush()
        return role

    async def test_grants_cannot_be_edited(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, _ = await _admin(session)
        builtin = await self._builtin(session)

        with pytest.raises(ConflictError) as exc:
            await RoleService(session, permissions).update_role(
                actor_for(person), builtin.id, grants=[perms.PROJECT_EDIT]
            )
        assert exc.value.code == "org.role_is_builtin"

    async def test_the_description_can_still_be_edited(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """설명과 2FA 요구는 시드가 손대지 않는다. 막을 이유가 없다."""
        person, _ = await _admin(session)
        builtin = await self._builtin(session)

        updated = await RoleService(session, permissions).update_role(
            actor_for(person), builtin.id, description="사내 규정 3.2", require_mfa=True
        )
        assert updated.description == "사내 규정 3.2"
        assert updated.require_mfa is True

    async def test_it_cannot_be_deleted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, _ = await _admin(session)
        builtin = await self._builtin(session)

        with pytest.raises(ConflictError) as exc:
            await RoleService(session, permissions).delete_role(actor_for(person), builtin.id)
        assert exc.value.code == "org.role_is_builtin"


class TestFixingACustomRole:
    async def test_grants_are_replaced_not_merged(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """목록 하나를 정본으로 둔다. 차이만 적용하면 빼려던 권한 하나가
        조용히 살아남는다 — 그게 훨씬 나쁜 실패다."""
        person, _ = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)

        role = await service.create_role(
            actor,
            name=f"Auditor {new_id()}",
            scope_kind="global",
            grants=[perms.PROJECT_VIEW, perms.PROJECT_CREATE],
        )
        await service.update_role(actor, role.id, grants=[perms.PROJECT_VIEW])

        rows = (
            await session.execute(
                select(PermissionGrant.permission).where(PermissionGrant.role_id == role.id)
            )
        ).scalars()
        assert sorted(rows) == [perms.PROJECT_VIEW]

    async def test_a_permission_the_scope_cannot_use_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """전역 전용 권한을 프로젝트 역할에 넣으면 저장은 되고 평가에서
        조용히 무시된다 — "줬는데 안 된다" 가 된다."""
        person, _ = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)

        role = await service.create_role(
            actor,
            name=f"Local {new_id()}",
            scope_kind="project",
            grants=[perms.PROJECT_VIEW],
        )
        with pytest.raises(ValidationError) as exc:
            # `org.project.create` 는 전역 전용이다.
            await service.update_role(actor, role.id, grants=[perms.PROJECT_CREATE])
        assert exc.value.code == "org.permission_scope_mismatch"

    async def test_a_global_role_may_carry_project_permissions(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**전역 역할은 좁은 스코프 권한을 담을 수 있다.**

        전역 할당은 모든 프로젝트·스페이스를 덮으므로(`acl_for` 가
        `is_global` 을 세우면 어떤 스코프에서도 통과한다) 정상이고, 시드의
        Administrator 가 바로 그 모양이다 — `issue.create` 는 프로젝트
        스코프 권한인데 전역 역할이 들고 있다.

        한동안 이 방향까지 막혀 있었다. 그러면 관리자가 역할 화면에서
        **시드 자신과 같은 역할을 만들 수 없다.** `test_seed_grants.py` 를
        쓰다가 드러났다.
        """
        from ieum.modules.issues import permissions as issue_perms

        person, _ = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)

        role = await service.create_role(
            actor,
            name=f"Global {new_id()}",
            scope_kind="global",
            grants=[perms.PROJECT_VIEW],
        )
        updated = await service.update_role(
            actor, role.id, grants=[perms.PROJECT_VIEW, issue_perms.ISSUE_CREATE]
        )
        assert updated.id == role.id

        # 만들 때도 같아야 한다 — 한쪽만 열어 두면 만들고 나서 고칠 수 없다.
        created = await service.create_role(
            actor,
            name=f"Global2 {new_id()}",
            scope_kind="global",
            grants=[issue_perms.ISSUE_CREATE],
        )
        assert created.scope_kind == "global"

    async def test_deleting_takes_the_assignments_with_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, _ = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)
        someone = await _person(session)

        role = await service.create_role(
            actor, name=f"Temp {new_id()}", scope_kind="global", grants=[perms.PROJECT_VIEW]
        )
        await service.assign(
            actor,
            role_id=role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=someone.id,
        )
        await session.flush()

        await service.delete_role(actor, role.id)
        await session.flush()

        rows = (
            await session.execute(select(RoleAssignment).where(RoleAssignment.role_id == role.id))
        ).scalars()
        assert list(rows) == []


class TestRevoking:
    """**주는 길만 있으면 그건 권한 관리가 아니다.**"""

    async def test_an_assignment_can_be_taken_back(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, _ = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)
        someone = await _person(session)

        role = await service.create_role(
            actor, name=f"Viewer {new_id()}", scope_kind="global", grants=[perms.PROJECT_VIEW]
        )
        assignment = await service.assign(
            actor,
            role_id=role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=someone.id,
        )
        await session.flush()

        assert len(await service.assignments(actor, role.id)) == 1
        await service.revoke(actor, assignment.id)
        await session.flush()
        assert await service.assignments(actor, role.id) == []

    async def test_an_unknown_assignment_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, _ = await _admin(session)
        with pytest.raises(NotFoundError):
            await RoleService(session, permissions).revoke(actor_for(person), new_id())

    async def test_you_cannot_revoke_your_own_admin_role(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """마지막 관리자가 자기 할당을 회수하면 되돌려 줄 사람이 없다."""
        person, role = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)

        mine = (await service.assignments(actor, role.id))[0]
        with pytest.raises(ConflictError) as exc:
            await service.revoke(actor, mine.id)
        assert exc.value.code == "org.cannot_revoke_own_admin"

    async def test_someone_elses_admin_assignment_can_be_revoked(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """자기 것만 막는다. 남의 관리자 자리를 못 거두면 퇴사 처리가 안 된다."""
        person, role = await _admin(session)
        actor = actor_for(person)
        service = RoleService(session, permissions)
        other = await _person(session)

        theirs = await service.assign(
            actor,
            role_id=role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=other.id,
        )
        await session.flush()

        await service.revoke(actor, theirs.id)
        await session.flush()
        assert [a.principal_id for a in await service.assignments(actor, role.id)] == [person.id]


class TestSelfLockout:
    async def test_you_cannot_strip_role_manage_from_your_only_source(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, role = await _admin(session)
        with pytest.raises(ConflictError) as exc:
            await RoleService(session, permissions).update_role(
                actor_for(person), role.id, grants=[perms.PROJECT_VIEW]
            )
        assert exc.value.code == "org.cannot_drop_own_role_manage"

    async def test_you_cannot_delete_your_only_source(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        person, role = await _admin(session)
        with pytest.raises(ConflictError) as exc:
            await RoleService(session, permissions).delete_role(actor_for(person), role.id)
        assert exc.value.code == "org.cannot_drop_own_role_manage"

    async def test_with_another_source_it_is_allowed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """다른 경로로도 관리자면 막을 이유가 없다. 막으면 역할을 정리할 수
        없고, 정리할 수 없는 것은 결국 쌓인다."""
        person, role = await _admin(session)
        await grant(
            session,
            principal_id=person.id,
            permissions_granted=(perms.ROLE_MANAGE,),
            scope=Scope.global_(),
        )

        updated = await RoleService(session, permissions).update_role(
            actor_for(person), role.id, grants=[perms.PROJECT_VIEW]
        )
        assert updated.id == role.id

    async def test_a_group_you_belong_to_counts_as_yours(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """역할은 그룹에도 붙는다. 그룹으로 관리자가 된 사람이 그 역할을
        비우면 결과는 같다 — 자기 발을 쏘는 것이다."""
        from ieum.modules.identity.models import UserGroup

        person = await _person(session)
        group = UserGroup(name=f"admins-{new_id()}", source="local")
        session.add(group)
        await session.flush()

        role = await grant(
            session,
            principal_id=group.id,
            principal_kind="group",
            permissions_granted=(perms.ROLE_MANAGE,),
            scope=Scope.global_(),
        )
        actor = actor_for(person, group_ids=frozenset({group.id}))

        with pytest.raises(ConflictError) as exc:
            await RoleService(session, permissions).update_role(actor, role.id, grants=[])
        assert exc.value.code == "org.cannot_drop_own_role_manage"
