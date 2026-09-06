"""모듈 공개 인터페이스(contracts) 테스트.

다른 모듈이 유일하게 의존하는 표면이다. 여기가 깨지면 호출하는 모듈 전부가
같이 깨지므로, 아직 호출자가 없어도 계약을 고정해 둔다.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ieum.config import Settings
from ieum.core.ids import new_id
from ieum.modules.identity import contracts as identity
from ieum.modules.identity.models import GroupMember, User, UserGroup
from ieum.modules.org import contracts as org
from ieum.modules.org.models import Project


async def _user(session: AsyncSession, **overrides: object) -> User:
    fields: dict[str, object] = {
        "email": f"u-{new_id()}@example.com",
        "display_name": "Tester",
        "status": "active",
    }
    fields.update(overrides)
    row = User(**fields)  # type: ignore[arg-type]
    session.add(row)
    await session.flush()
    return row


class TestIdentityContracts:
    async def test_get_user_returns_dto_not_orm(self, session: AsyncSession) -> None:
        """ORM 모델이 새어나가면 모듈 경계가 무의미해진다."""
        user = await _user(session)
        ref = await identity.get_user(session, user.id)
        assert isinstance(ref, identity.UserRef)
        assert not isinstance(ref, User)
        assert ref.email == user.email

    async def test_get_user_missing(self, session: AsyncSession) -> None:
        assert await identity.get_user(session, new_id()) is None

    async def test_get_user_by_email_is_case_insensitive(self, session: AsyncSession) -> None:
        user = await _user(session)
        found = await identity.get_user_by_email(session, user.email.upper())
        assert found is not None and found.id == user.id

    async def test_group_ids_for(self, session: AsyncSession) -> None:
        user = await _user(session)
        group = UserGroup(name=f"g-{new_id()}")
        session.add(group)
        await session.flush()
        session.add(GroupMember(group_id=group.id, user_id=user.id))
        await session.flush()

        assert await identity.group_ids_for(session, user.id) == frozenset({group.id})

    async def test_load_actor_includes_groups(self, session: AsyncSession) -> None:
        user = await _user(session, locale="ko", timezone="Asia/Seoul")
        group = UserGroup(name=f"g-{new_id()}")
        session.add(group)
        await session.flush()
        session.add(GroupMember(group_id=group.id, user_id=user.id))
        await session.flush()

        actor = await identity.load_actor(session, user.id)
        assert actor is not None
        assert actor.locale == "ko"
        assert actor.timezone == "Asia/Seoul"
        assert actor.principal_ids == frozenset({user.id, group.id})

    async def test_load_actor_missing(self, session: AsyncSession) -> None:
        assert await identity.load_actor(session, new_id()) is None

    async def test_suspended_user_is_not_active(self, session: AsyncSession) -> None:
        user = await _user(session, status="suspended")
        ref = await identity.get_user(session, user.id)
        assert ref is not None and ref.is_active is False


class TestOrgContracts:
    async def test_get_project_returns_dto(self, session: AsyncSession) -> None:
        project = Project(key="ENG", name="Engineering")
        session.add(project)
        await session.flush()

        ref = await org.get_project(session, project.id)
        assert isinstance(ref, org.ProjectRef)
        assert ref.key == "ENG" and ref.is_archived is False

    async def test_get_project_by_key_normalizes_case(self, session: AsyncSession) -> None:
        session.add(Project(key="ENG", name="Engineering"))
        await session.flush()
        found = await org.get_project_by_key(session, "eng")
        assert found is not None and found.key == "ENG"

    async def test_missing_project(self, session: AsyncSession) -> None:
        assert await org.get_project(session, new_id()) is None
        assert await org.get_project_by_key(session, "NOPE") is None

    async def test_issue_numbers_increment(self, session: AsyncSession) -> None:
        project = Project(key="ENG", name="Engineering")
        session.add(project)
        await session.flush()

        numbers = [await org.next_issue_number(session, project.id) for _ in range(3)]
        assert numbers == [1, 2, 3]

    async def test_issue_number_for_missing_project(self, session: AsyncSession) -> None:
        with pytest.raises(LookupError):
            await org.next_issue_number(session, new_id())

    @pytest.mark.integration
    async def test_concurrent_numbering_never_duplicates(
        self, engine: object, settings: Settings
    ) -> None:
        """동시 채번에 중복이 나오면 이슈 키가 겹친다.

        UPDATE ... RETURNING 이 원자적인지 실제 커넥션 여러 개로 확인한다.
        롤백 세션(session 픽스처)으로는 동시성을 볼 수 없어 여기서만 커밋한다.
        """
        factory = async_sessionmaker(bind=engine, expire_on_commit=False)  # type: ignore[arg-type]

        async with factory() as setup:
            project = Project(key=f"C{new_id().hex[:6].upper()}", name="Concurrent")
            setup.add(project)
            await setup.commit()
            project_id = project.id

        async def claim() -> int:
            async with factory() as s:
                number = await org.next_issue_number(s, project_id)
                await s.commit()
                return number

        numbers = await asyncio.gather(*(claim() for _ in range(10)))
        assert sorted(numbers) == list(range(1, 11))

        async with factory() as cleanup:
            target = await cleanup.get(Project, project_id)
            if target is not None:
                await cleanup.delete(target)
                await cleanup.commit()
