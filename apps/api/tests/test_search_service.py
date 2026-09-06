"""통합 검색. 실제 Postgres(PGroonga)를 쓴다.

여기서 제일 중요한 건 랭킹이 아니라 **권한**이다. 검색이 권한을 우회하면
링크를 몰라도 제목과 본문 일부가 새어 나간다. 그래서 스코프 밖의 것과
객체 수준으로 제한된 것이 결과에 들지 않는지를 집중해서 본다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope
from ieum.core.time import utcnow
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as issue_perms
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository
from ieum.modules.search import contracts as search
from ieum.modules.search.service import SearchService
from ieum.modules.wiki import permissions as wiki_perms
from ieum.modules.wiki.models import Space

pytestmark = pytest.mark.integration


@pytest.fixture
def permissions() -> PermissionService:
    return PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> AsyncIterator[User]:
    row = User(email=f"s-{new_id()}@example.com", display_name="Seeker", status="active")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def project(session: AsyncSession) -> AsyncIterator[Project]:
    # 진짜 행이어야 한다. acl_for 가 프로젝트 계층을 펼쳐 보므로, 없는 id
    # 로는 스코프가 비어 나온다.
    row = Project(key=f"P{new_id().hex[:6].upper()}", name="Search")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def space(session: AsyncSession) -> AsyncIterator[Space]:
    row = Space(key=f"S{new_id().hex[:6].upper()}", name="Docs")
    session.add(row)
    await session.flush()
    yield row


def actor_for(user: User, *, group_ids: frozenset[UUID] = frozenset()) -> Actor:
    return Actor(
        user_id=user.id,
        email=user.email,
        is_active=True,
        mfa_satisfied_at=utcnow(),
        group_ids=group_ids,
    )


async def grant(
    session: AsyncSession, *, principal_id: UUID, permission: str, scope: Scope
) -> Role:
    repo = RoleRepository(session)
    role = Role(name=f"role-{new_id()}", scope_kind=scope.kind.value)
    repo.add(role)
    await session.flush()
    repo.grant(role.id, permission)
    repo.assign(role_id=role.id, scope=scope, principal_kind="user", principal_id=principal_id)
    await session.flush()
    return role


async def index_page(
    session: AsyncSession,
    space: Space,
    *,
    title: str,
    body: str,
    restricted_to: list[UUID] | None = None,
) -> UUID:
    entity_id = new_id()
    await search.index_document(
        session,
        kind=search.PAGE,
        entity_id=entity_id,
        scope_kind="space",
        scope_id=space.id,
        ref=f"{space.key}/{title}",
        title=title,
        body=body,
        restricted_to=restricted_to,
        updated_at=utcnow(),
    )
    await session.flush()
    return entity_id


class TestFindingThings:
    async def test_finds_korean_by_a_partial_word(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        """형태소 분석이 되는지. 이게 PGroonga 를 고른 이유다 (ADR-0005)."""
        await index_page(session, space, title="배포 절차", body="마이그레이션을 검토한다.")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )

        found = await SearchService(session, permissions).search(
            actor_for(user), query="마이그레이션"
        )
        assert [h.title for h in found.hits] == ["배포 절차"]

    async def test_matches_the_title_too(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        await index_page(session, space, title="롤백 계획", body="딴 얘기.")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(actor_for(user), query="롤백")
        assert len(found.hits) == 1

    async def test_two_words_mean_and(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        await index_page(session, space, title="A", body="사과와 배가 있다.")
        await index_page(session, space, title="B", body="사과만 있다.")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(actor_for(user), query="사과 배가")
        assert [h.title for h in found.hits] == ["A"]

    async def test_an_empty_query_finds_nothing(
        self, session: AsyncSession, permissions: PermissionService, user: User
    ) -> None:
        # 빈 질의에 전부 돌려주면 첫 화면에서 남의 문서 제목이 쏟아진다.
        found = await SearchService(session, permissions).search(actor_for(user), query="   ")
        assert (found.hits, found.total) == ([], 0)

    async def test_a_broken_query_is_not_an_error(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        """질의 구문이 깨져도 500 이 아니라 0건이다."""
        await index_page(session, space, title="A", body="본문")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(actor_for(user), query="((")
        assert found.hits == []

    async def test_the_snippet_shows_the_match(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        body = "앞쪽 이야기가 길게 이어진다. " * 8 + "여기에 롤백 계획이 있다."
        await index_page(session, space, title="긴 문서", body=body)
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(actor_for(user), query="롤백")
        assert "롤백" in found.hits[0].snippet
        # 발췌지 본문이 아니다.
        assert len(found.hits[0].snippet) < len(body)


class TestPermissions:
    """검색이 권한을 우회하면 링크를 몰라도 내용이 샌다."""

    async def test_no_permission_means_no_results(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        await index_page(session, space, title="비밀", body="아무나 보면 안 된다.")
        found = await SearchService(session, permissions).search(actor_for(user), query="비밀")
        assert (found.hits, found.total) == ([], 0)

    async def test_another_space_is_invisible(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        elsewhere = Space(key=f"X{new_id().hex[:6].upper()}", name="Other")
        session.add(elsewhere)
        await session.flush()
        await index_page(session, space, title="보이는 문서", body="공통낱말")
        await index_page(session, elsewhere, title="안 보이는 문서", body="공통낱말")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )

        found = await SearchService(session, permissions).search(actor_for(user), query="공통낱말")
        assert [h.title for h in found.hits] == ["보이는 문서"]
        # 총계도 걸러진 뒤의 숫자여야 한다. 안 그러면 "1건 더 있다"가 샌다.
        assert found.total == 1

    async def test_object_restriction_hides_it_even_inside_the_scope(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        """스코프 권한만 보면 제한된 문서가 검색으로 샌다."""
        allowed = new_id()
        await index_page(
            session, space, title="제한 문서", body="공통낱말", restricted_to=[allowed]
        )
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(actor_for(user), query="공통낱말")
        assert found.hits == []

    async def test_a_listed_principal_sees_the_restricted_page(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        await index_page(
            session, space, title="제한 문서", body="공통낱말", restricted_to=[user.id]
        )
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(actor_for(user), query="공통낱말")
        assert [h.title for h in found.hits] == ["제한 문서"]

    async def test_a_group_membership_satisfies_the_restriction(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        group_id = new_id()
        await index_page(session, space, title="팀 문서", body="공통낱말", restricted_to=[group_id])
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(
            actor_for(user, group_ids=frozenset({group_id})), query="공통낱말"
        )
        assert [h.title for h in found.hits] == ["팀 문서"]

    async def test_kinds_use_their_own_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        project: Project,
    ) -> None:
        """문서 권한만 있는 사람에게 이슈가 나오면 안 된다."""
        project_id = project.id
        await index_page(session, space, title="문서 쪽", body="공통낱말")
        await search.index_document(
            session,
            kind=search.ISSUE,
            entity_id=new_id(),
            scope_kind="project",
            scope_id=project_id,
            ref="ENG-1",
            title="이슈 쪽",
            body="공통낱말",
            restricted_to=None,
            updated_at=utcnow(),
        )
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )

        found = await SearchService(session, permissions).search(actor_for(user), query="공통낱말")
        assert [h.kind for h in found.hits] == ["page"]

    async def test_both_kinds_come_back_when_both_are_allowed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        project: Project,
    ) -> None:
        project_id = project.id
        await index_page(session, space, title="문서 쪽", body="공통낱말")
        await search.index_document(
            session,
            kind=search.ISSUE,
            entity_id=new_id(),
            scope_kind="project",
            scope_id=project_id,
            ref="ENG-1",
            title="이슈 쪽",
            body="공통낱말",
            restricted_to=None,
            updated_at=utcnow(),
        )
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        await grant(
            session,
            principal_id=user.id,
            permission=issue_perms.ISSUE_VIEW,
            scope=Scope.project(project_id),
        )

        found = await SearchService(session, permissions).search(actor_for(user), query="공통낱말")
        assert {h.kind for h in found.hits} == {"page", "issue"}

    async def test_kind_filter_narrows_the_result(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        await index_page(session, space, title="문서 쪽", body="공통낱말")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        found = await SearchService(session, permissions).search(
            actor_for(user), query="공통낱말", kinds=("issue",)
        )
        assert found.hits == []


class TestIndexMaintenance:
    async def test_reindexing_replaces_the_row(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        """같은 것을 두 번 색인해도 결과가 두 줄이 되지 않는다."""
        entity_id = new_id()
        for body in ("첫 번째 내용", "고친 내용"):
            await search.index_document(
                session,
                kind=search.PAGE,
                entity_id=entity_id,
                scope_kind="space",
                scope_id=space.id,
                ref="X/y",
                title="문서",
                body=body,
                restricted_to=None,
                updated_at=utcnow(),
            )
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )

        service = SearchService(session, permissions)
        assert (await service.search(actor_for(user), query="고친")).total == 1
        # 옛 본문은 더 이상 찾히지 않는다.
        assert (await service.search(actor_for(user), query="첫")).total == 0

    async def test_removing_takes_it_out(
        self, session: AsyncSession, permissions: PermissionService, user: User, space: Space
    ) -> None:
        entity_id = await index_page(session, space, title="지울 것", body="공통낱말")
        await grant(
            session,
            principal_id=user.id,
            permission=wiki_perms.PAGE_VIEW,
            scope=Scope.space(space.id),
        )
        await search.remove_document(session, kind=search.PAGE, entity_id=entity_id)
        await session.flush()

        found = await SearchService(session, permissions).search(actor_for(user), query="공통낱말")
        assert found.hits == []


class TestPermissionNamesStayInSync:
    """search 는 남의 권한 상수를 import 하지 않는다(모듈 경계).

    그래서 문자열이 어긋나도 조용하다 — 검색이 늘 0건이 되고, 이유를 찾기까지
    한참 걸린다. 여기서 실제 상수와 대조해 고정한다.
    """

    def test_kind_permissions_match_the_modules(self) -> None:
        from ieum.modules.search.service import KIND_PERMISSIONS

        assert KIND_PERMISSIONS == {
            "issue": issue_perms.ISSUE_VIEW,
            "page": wiki_perms.PAGE_VIEW,
        }

    def test_kind_permissions_are_registered(self) -> None:
        """등록되지 않은 권한으로 acl_for 를 부르면 KeyError 로 죽는다."""
        import ieum.main  # noqa: F401 - 모든 모듈의 권한을 등록시킨다
        from ieum.core.permissions import registry
        from ieum.modules.search.service import KIND_PERMISSIONS

        known = {d.key for d in registry.all()}
        assert set(KIND_PERMISSIONS.values()) <= known
