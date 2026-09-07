"""그룹 관리.

그룹은 권한을 사람 단위로 만지지 않게 하는 자리다 — 역할을 그룹에 붙이고
사람은 그룹에 넣는다. 화면이 없던 동안 SSO 가 동기화한 그룹은 어디에도
보이지 않았고, 매핑이 도는지 확인할 방법도 없었다.

여기서 고정하는 것은 두 가지다: **되돌릴 수 있는가**(지운 그룹의 역할 할당은
어떻게 되는가), 그리고 **되돌려질 변경을 받아 주지 않는가**(IdP 가 관리하는
그룹을 손으로 고치면 다음 로그인이 원상복구한다).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.modules.identity import permissions as perms
from ieum.modules.identity.models import GroupMember, User, UserGroup
from ieum.modules.identity.service import GroupService
from ieum.modules.org.models import RoleAssignment
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


async def _admin(session: AsyncSession) -> User:
    row = User(email=f"admin-{new_id()}@example.com", display_name="Admin", status="active")
    session.add(row)
    await session.flush()
    await grant(
        session,
        principal_id=row.id,
        permissions_granted=(perms.GROUP_MANAGE,),
        scope=Scope.global_(),
    )
    return row


async def _person(session: AsyncSession, *, is_customer: bool = False) -> User:
    row = User(
        email=f"p-{new_id()}@example.com",
        display_name="Person",
        status="active",
        is_customer=is_customer,
    )
    session.add(row)
    await session.flush()
    return row


class TestPermission:
    async def test_a_stranger_cannot_see_the_groups(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await GroupService(session, permissions).list_all(actor_for(nobody))


class TestCreateAndList:
    async def test_a_new_group_shows_up_with_zero_members(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """빈 그룹이 목록에서 빠지면 방금 만든 것이 안 보인다 — 사용자는
        만들기가 실패한 줄로 읽는다."""
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))

        created = await service.create(actor, name="  Platform  ", description=None)
        # 앞뒤 공백은 깎는다. 안 깎으면 "Platform" 과 " Platform" 이 다른
        # 그룹이 되고, 이름으로 찾는 IdP 동기화가 둘 중 하나를 못 본다.
        assert created.name == "Platform"
        assert created.source == "local"

        rows = await service.list_all(actor)
        assert [(g.name, count) for g, count in rows] == [("Platform", 0)]

    async def test_the_same_name_twice_is_a_conflict_not_a_crash(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        await service.create(actor, name="Platform", description=None)

        with pytest.raises(ConflictError) as exc:
            await service.create(actor, name="Platform", description=None)
        assert exc.value.code == "identity.group_name_taken"


class TestMembers:
    async def test_add_then_remove(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await service.create(actor, name="Platform", description=None)
        person = await _person(session)

        await service.add_member(actor, group.id, person.id)
        assert [u.id for u in await service.members(actor, group.id)] == [person.id]

        # 두 번 넣어도 한 명이다. 유일 제약에 부딪혀 500 이 나면 안 된다.
        await service.add_member(actor, group.id, person.id)
        assert len(await service.members(actor, group.id)) == 1

        await service.remove_member(actor, group.id, person.id)
        assert await service.members(actor, group.id) == []

    async def test_a_portal_customer_cannot_be_put_in_an_internal_group(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객은 /portal/* 만 본다 (auth.md 5절). 내부 그룹에 넣으면 그
        그룹에 붙은 역할로 내부 권한이 새어 나간다."""
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await service.create(actor, name="Platform", description=None)
        customer = await _person(session, is_customer=True)

        with pytest.raises(ConflictError) as exc:
            await service.add_member(actor, group.id, customer.id)
        assert exc.value.code == "identity.group_customer_member"

    async def test_an_unknown_person_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await service.create(actor, name="Platform", description=None)

        with pytest.raises(NotFoundError):
            await service.add_member(actor, group.id, new_id())


class TestIdpManagedGroups:
    """IdP 가 만든 그룹.

    동기화(`SsoService._sync_groups`)는 클레임에 없는 IdP 그룹에서 사람을
    **뺀다.** 그래서 손으로 넣은 멤버는 다음 로그인에 사라지고, 이름을 바꾼
    그룹은 매칭에서 빠져 텅 빈다. 받아 주면 화면은 성공을 보여 주고 결과는
    없어진다 — 그게 가장 나쁜 실패다.
    """

    async def _managed(self, session: AsyncSession) -> UserGroup:
        group = UserGroup(name="engineering", source="idp")
        session.add(group)
        await session.flush()
        return group

    async def test_it_is_listed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**보여야 한다.** 이게 그룹 매핑이 돌고 있다는 유일한 증거다."""
        actor = actor_for(await _admin(session))
        await self._managed(session)
        rows = await GroupService(session, permissions).list_all(actor)
        assert [(g.name, g.source) for g, _ in rows] == [("engineering", "idp")]

    async def test_members_cannot_be_edited_by_hand(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await self._managed(session)
        person = await _person(session)

        with pytest.raises(ConflictError) as exc:
            await service.add_member(actor, group.id, person.id)
        assert exc.value.code == "identity.group_is_managed"

        with pytest.raises(ConflictError):
            await service.remove_member(actor, group.id, person.id)

    async def test_renaming_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이름은 IdP 가 보내는 클레임 값이다."""
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await self._managed(session)

        with pytest.raises(ConflictError) as exc:
            await service.update(actor, group.id, name="Engineering", description=None)
        assert exc.value.code == "identity.group_is_managed"

    async def test_the_description_can_still_be_edited(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """설명은 IdP 가 안 준다. 우리 메모라 막을 이유가 없다."""
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await self._managed(session)

        updated = await service.update(actor, group.id, name=None, description="From Okta")
        assert updated.description == "From Okta"

    async def test_it_can_be_deleted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """다음 로그인이 다시 만들지만, 지우는 길을 막으면 매핑을 끈 뒤에도
        옛 그룹이 영구히 목록에 남는다."""
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await self._managed(session)

        await service.delete(actor, group.id)
        assert await service.list_all(actor) == []


class TestDeleteTakesTheRoleAssignmentsWithIt:
    """`role_assignment.principal_id` 는 FK 가 아니다 — 사용자일 수도 그룹일
    수도 있어 한쪽으로 걸 수 없다. 그래서 그룹을 지우면 할당 행이 남는다."""

    async def test_assignments_are_dropped(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await service.create(actor, name="Platform", description=None)
        await grant(
            session,
            principal_id=group.id,
            principal_kind="group",
            permissions_granted=(perms.AUDIT_VIEW,),
            scope=Scope.global_(),
        )

        before = (
            await session.execute(
                select(RoleAssignment).where(RoleAssignment.principal_id == group.id)
            )
        ).scalars()
        assert len(list(before)) == 1

        await service.delete(actor, group.id)

        after = (
            await session.execute(
                select(RoleAssignment).where(RoleAssignment.principal_id == group.id)
            )
        ).scalars()
        assert list(after) == []

    async def test_members_go_with_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """멤버 행은 FK 의 ON DELETE CASCADE 가 정리한다. 남으면 지운 그룹의
        멤버십이 권한 계산에 계속 실려 온다."""
        service = GroupService(session, permissions)
        actor = actor_for(await _admin(session))
        group = await service.create(actor, name="Platform", description=None)
        person = await _person(session)
        await service.add_member(actor, group.id, person.id)

        await service.delete(actor, group.id)
        await session.flush()

        rows = (
            await session.execute(select(GroupMember).where(GroupMember.group_id == group.id))
        ).scalars()
        assert list(rows) == []
