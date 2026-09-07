"""wiki 서비스 테스트. 실제 Postgres 를 쓴다.

핵심은 트리와 제한이다. 트리가 틀리면 문서가 사라지고, 제한이 틀리면
"보이면 안 되는 게 보이는" 사고가 난다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.attachments import Attachment, AttachmentService
from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    NotFoundError,
    OptimisticLockError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.outbox import OutboxEvent
from ieum.core.pagination import PageRequest
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.core.storage import ObjectStore
from ieum.core.time import utcnow
from ieum.modules.identity.models import GroupMember, User, UserGroup
from ieum.modules.org.models import Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository
from ieum.modules.wiki import attachments as wiki_attachments
from ieum.modules.wiki import permissions as perms
from ieum.modules.wiki import service as service_module
from ieum.modules.wiki.models import Space
from ieum.modules.wiki.portable import SKIPPED_MANIFEST
from ieum.modules.wiki.service import (
    NewPage,
    PageCommentService,
    PageRestrictionGuard,
    PageService,
    SpaceService,
)
from ieum.modules.wiki.slug import slugify, unique_slug


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    # 라우터 없이 서비스만 쓰는 테스트라 가드를 직접 꽂는다. main 이 하는 일과 같다.
    from ieum.modules.wiki.contracts import page_model

    service.register_guard(page_model(), PageRestrictionGuard())
    return service


@pytest_asyncio.fixture
async def user(session: AsyncSession) -> AsyncIterator[User]:
    row = User(email=f"u-{new_id()}@example.com", display_name="Writer", status="active")
    session.add(row)
    await session.flush()
    yield row


@pytest_asyncio.fixture
async def other(session: AsyncSession) -> AsyncIterator[User]:
    row = User(email=f"o-{new_id()}@example.com", display_name="Other", status="active")
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
    session: AsyncSession,
    *,
    principal_id: UUID,
    permissions_granted: tuple[str, ...],
    scope: Scope,
    principal_kind: str = "user",
) -> Role:
    repo = RoleRepository(session)
    role = Role(name=f"role-{new_id()}", scope_kind=scope.kind.value)
    repo.add(role)
    await session.flush()
    for permission in permissions_granted:
        repo.grant(role.id, permission)
    repo.assign(
        role_id=role.id, scope=scope, principal_kind=principal_kind, principal_id=principal_id
    )
    await session.flush()
    return role


@pytest_asyncio.fixture
async def space(session: AsyncSession) -> AsyncIterator[Space]:
    row = Space(key=f"S{new_id().hex[:6].upper()}", name="Docs")
    session.add(row)
    await session.flush()
    yield row


async def full_access(session: AsyncSession, user: User, space: Space) -> Actor:
    await grant(
        session,
        principal_id=user.id,
        permissions_granted=(
            perms.PAGE_VIEW,
            perms.PAGE_CREATE,
            perms.PAGE_EDIT,
            perms.PAGE_DELETE,
            perms.PAGE_MOVE,
            perms.PAGE_RESTRICT,
            perms.SPACE_ADMIN,
        ),
        scope=Scope.space(space.id),
    )
    return actor_for(user)


class TestSlug:
    @pytest.mark.parametrize(
        ("title", "expected"),
        [
            ("Deploy Runbook", "deploy-runbook"),
            ("  Spaces   Everywhere  ", "spaces-everywhere"),
            ("Slashes/And\\Things", "slashesandthings"),
            ("배포 절차", "배포-절차"),
            ("!!!", "untitled"),
            ("", "untitled"),
        ],
    )
    def test_slugify(self, title: str, expected: str) -> None:
        assert slugify(title) == expected

    def test_korean_is_kept_not_romanized(self) -> None:
        """로마자로 옮기면 원래 제목을 되짚을 수 없고 표기법마다 달라진다."""
        assert slugify("한글 제목") == "한글-제목"

    def test_nfd_and_nfc_give_the_same_slug(self) -> None:
        # macOS 는 자모를 분리해서 보낸다. 그대로 두면 눈에 같은 두 slug 이 갈린다.
        assert slugify("배포") == slugify("배포".encode().decode())

    def test_unique_slug_appends_a_number(self) -> None:
        assert unique_slug("deploy", {"deploy"}) == "deploy-2"
        assert unique_slug("deploy", {"deploy", "deploy-2"}) == "deploy-3"

    def test_unique_slug_respects_the_length_cap(self) -> None:
        base = "a" * 200
        result = unique_slug(base, {base})
        assert len(result) <= 200
        assert result.endswith("-2")


class TestSpaceCreation:
    async def test_requires_global_create_permission(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        with pytest.raises(PermissionDeniedError):
            await SpaceService(session, permissions).create(
                actor_for(user), key="ENG", name="Engineering"
            )

    async def test_normalizes_the_key(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.SPACE_CREATE,),
            scope=Scope.global_(),
        )
        space = await SpaceService(session, permissions).create(
            actor_for(user), key="  eng  ", name="  Engineering  "
        )
        assert (space.key, space.name) == ("ENG", "Engineering")

    @pytest.mark.parametrize("bad", ["e", "1ENG", "eng-1", "TOOOOOOOOOOOOOOOOOLONG", ""])
    async def test_rejects_bad_keys(
        self, session: AsyncSession, user: User, permissions: PermissionService, bad: str
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.SPACE_CREATE,),
            scope=Scope.global_(),
        )
        with pytest.raises(ValidationError) as exc:
            await SpaceService(session, permissions).create(actor_for(user), key=bad, name="X")
        assert exc.value.code == "wiki.invalid_space_key"

    async def test_rejects_duplicate_key(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.SPACE_CREATE,),
            scope=Scope.global_(),
        )
        service = SpaceService(session, permissions)
        await service.create(actor_for(user), key="ENG", name="First")
        with pytest.raises(ConflictError) as exc:
            await service.create(actor_for(user), key="eng", name="Second")
        assert exc.value.code == "wiki.space_key_taken"

    async def test_lists_only_visible_spaces(
        self, session: AsyncSession, user: User, permissions: PermissionService
    ) -> None:
        seen = Space(key="SEEN", name="Visible")
        hidden = Space(key="HIDDEN", name="Hidden")
        session.add_all([seen, hidden])
        await session.flush()
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(seen.id),
        )
        page = await SpaceService(session, permissions).list_for(
            actor_for(user), PageRequest(limit=50)
        )
        assert [s.key for s in page.items] == ["SEEN"]


class TestPageCreation:
    async def test_creates_a_draft_by_default(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """쓰다 만 문서가 트리에 게시된 채로 뜨면 곤란하다."""
        actor = await full_access(session, user, space)
        view = await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Deploy Runbook", body="# Hello")
        )
        assert view.page.status == "draft"
        assert view.page.current_version_id is None
        # 초안이어도 본문은 읽힌다 — 편집 화면이 이걸 연다.
        assert view.body.startswith("# Hello")

    async def test_publishes_when_asked(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        view = await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Live", publish=True)
        )
        assert view.page.status == "published"
        assert view.page.current_version_id is not None

    async def test_path_follows_the_slug_chain(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        parent = await service.create(actor, NewPage(space_id=space.id, title="Deploy"))
        child = await service.create(
            actor,
            NewPage(space_id=space.id, title="Rollback", parent_id=parent.page.id),
        )
        assert (parent.page.path, child.page.path) == ("deploy", "deploy/rollback")

    async def test_same_title_gets_a_distinct_slug(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """제목이 겹치는 건 흔한 일이다. 여기서 막으면 제목을 억지로 비튼다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        first = await service.create(actor, NewPage(space_id=space.id, title="Notes"))
        second = await service.create(actor, NewPage(space_id=space.id, title="Notes"))
        assert first.page.slug == "notes"
        assert second.page.slug == "notes-2"

    async def test_body_is_normalized_on_save(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """정규화가 없으면 왕복마다 diff 가 오염돼 버전 비교가 쓸모없어진다."""
        actor = await full_access(session, user, space)
        view = await PageService(session, permissions).create(
            actor,
            NewPage(space_id=space.id, title="Messy", body="*   loose   \n*   list   "),
        )
        assert view.body == "- loose\n- list"

    async def test_requires_create_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )
        with pytest.raises(PermissionDeniedError):
            await PageService(session, permissions).create(
                actor_for(user), NewPage(space_id=space.id, title="Nope")
            )

    async def test_parent_from_another_space_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        elsewhere = Space(key="OTHER", name="Other")
        session.add(elsewhere)
        await session.flush()
        service = PageService(session, permissions)

        # 다른 스페이스에는 권한이 없어 서비스로 만들 수 없다. 행을 직접 넣는다.
        from ieum.modules.wiki.models import Page

        stray = Page(space_id=elsewhere.id, path="x", slug="x", title="X")
        session.add(stray)
        await session.flush()

        with pytest.raises(ValidationError) as exc:
            await service.create(
                actor, NewPage(space_id=space.id, title="Child", parent_id=stray.id)
            )
        assert exc.value.code == "wiki.parent_not_found"


class TestVersions:
    async def test_editing_creates_a_new_version(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """판을 덮어쓰면 이력이 거짓말이 된다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        view = await service.create(
            actor, NewPage(space_id=space.id, title="Doc", body="first", publish=True)
        )
        await service.update(actor, view.page.id, body="second")

        history = await service.history(actor, view.page.id)
        assert [v.number for v in history] == [2, 1]
        assert [v.body for v in history] == ["second", "first"]

    async def test_no_new_version_when_nothing_changed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        view = await service.create(
            actor, NewPage(space_id=space.id, title="Doc", body="same", publish=True)
        )
        await service.update(actor, view.page.id, body="same", title="Doc")
        assert len(await service.history(actor, view.page.id)) == 1

    async def test_published_page_shows_the_new_version(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        view = await service.create(
            actor, NewPage(space_id=space.id, title="Doc", body="v1", publish=True)
        )
        after = await service.update(actor, view.page.id, body="v2")
        assert after.body == "v2"
        assert after.current is not None
        assert after.current.number == 2

    async def test_restore_adds_a_version_instead_of_rewinding(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """되감으면 그 사이 이력이 사라진다. 복원도 편집의 하나다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        view = await service.create(
            actor, NewPage(space_id=space.id, title="Doc", body="v1", publish=True)
        )
        await service.update(actor, view.page.id, body="v2")
        restored = await service.restore(actor, view.page.id, 1)

        assert restored.body == "v1"
        history = await service.history(actor, view.page.id)
        assert [v.number for v in history] == [3, 2, 1]
        assert history[0].message == "1판으로 복원"

    async def test_restore_unknown_version_is_not_found(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        view = await service.create(actor, NewPage(space_id=space.id, title="Doc"))
        with pytest.raises(NotFoundError):
            await service.restore(actor, view.page.id, 99)

    async def test_stale_version_conflicts(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        view = await service.create(actor, NewPage(space_id=space.id, title="Doc", body="a"))
        await service.update(actor, view.page.id, body="b")
        with pytest.raises(OptimisticLockError):
            await service.update(actor, view.page.id, body="c", expected_version=1)


class TestTree:
    async def test_tree_returns_the_whole_space(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        top = await service.create(actor, NewPage(space_id=space.id, title="Top"))
        await service.create(
            actor, NewPage(space_id=space.id, title="Under", parent_id=top.page.id)
        )
        rows = await service.tree(actor, space.id)
        assert {p.path for p in rows} == {"top", "top/under"}

    async def test_archived_pages_leave_the_tree(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        top = await service.create(actor, NewPage(space_id=space.id, title="Top"))
        child = await service.create(
            actor, NewPage(space_id=space.id, title="Under", parent_id=top.page.id)
        )
        await service.archive(actor, top.page.id)

        assert await service.tree(actor, space.id) == []
        # 후손도 함께 간다 — 부모 없는 문서를 남기지 않는다.
        reloaded = await service.get(actor, child.page.id)
        assert reloaded.page.archived_at is not None

    async def test_move_rewrites_descendant_paths(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        a = await service.create(actor, NewPage(space_id=space.id, title="A"))
        b = await service.create(actor, NewPage(space_id=space.id, title="B"))
        child = await service.create(
            actor, NewPage(space_id=space.id, title="Child", parent_id=a.page.id)
        )
        grandchild = await service.create(
            actor, NewPage(space_id=space.id, title="Grand", parent_id=child.page.id)
        )

        await service.move(actor, child.page.id, new_parent_id=b.page.id)

        assert (await service.get(actor, child.page.id)).page.path == "b/child"
        # 후손 경로도 따라온다. 안 따라오면 트리가 끊긴다.
        assert (await service.get(actor, grandchild.page.id)).page.path == "b/child/grand"

    async def test_move_to_root(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        parent = await service.create(actor, NewPage(space_id=space.id, title="Parent"))
        child = await service.create(
            actor, NewPage(space_id=space.id, title="Child", parent_id=parent.page.id)
        )
        moved = await service.move(actor, child.page.id, new_parent_id=None)
        assert (moved.page.path, moved.page.parent_id) == ("child", None)

    async def test_cannot_move_into_self(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(actor, NewPage(space_id=space.id, title="A"))
        with pytest.raises(ValidationError) as exc:
            await service.move(actor, page.page.id, new_parent_id=page.page.id)
        assert exc.value.code == "wiki.move_into_self"

    async def test_cannot_move_into_own_descendant(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """후손 아래로 옮기면 트리가 끊긴 고리가 된다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        top = await service.create(actor, NewPage(space_id=space.id, title="Top"))
        child = await service.create(
            actor, NewPage(space_id=space.id, title="Child", parent_id=top.page.id)
        )
        with pytest.raises(ValidationError) as exc:
            await service.move(actor, top.page.id, new_parent_id=child.page.id)
        assert exc.value.code == "wiki.move_into_descendant"

    async def test_move_resolves_slug_collision(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        target = await service.create(actor, NewPage(space_id=space.id, title="Target"))
        await service.create(
            actor, NewPage(space_id=space.id, title="Notes", parent_id=target.page.id)
        )
        loose = await service.create(actor, NewPage(space_id=space.id, title="Notes"))

        moved = await service.move(actor, loose.page.id, new_parent_id=target.page.id)
        assert moved.page.path == "target/notes-2"

    async def test_move_needs_move_permission_not_just_edit(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        """편집자에게 구조 변경까지 자동으로 주면 실수 한 번이 넓게 퍼진다."""
        owner = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(owner, NewPage(space_id=space.id, title="A"))

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW, perms.PAGE_EDIT),
            scope=Scope.space(space.id),
        )
        with pytest.raises(PermissionDeniedError):
            await service.move(actor_for(other), page.page.id, new_parent_id=None)


class TestRestrictions:
    async def test_view_restriction_hides_the_page(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(actor, NewPage(space_id=space.id, title="Secret"))
        await service.set_restrictions(
            actor, page.page.id, mode="view", principals=[("user", user.id)]
        )

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )
        with pytest.raises(PermissionDeniedError):
            await service.get(actor_for(other), page.page.id)
        # 지정된 사람은 그대로 본다.
        assert (await service.get(actor, page.page.id)).page.id == page.page.id

    async def test_restriction_is_inherited_by_descendants(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        """상위를 못 보면 그 아래도 못 본다. 아니면 링크만 알면 안쪽이 열린다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        top = await service.create(actor, NewPage(space_id=space.id, title="Locked"))
        child = await service.create(
            actor, NewPage(space_id=space.id, title="Inside", parent_id=top.page.id)
        )
        await service.set_restrictions(
            actor, top.page.id, mode="view", principals=[("user", user.id)]
        )

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )
        with pytest.raises(PermissionDeniedError):
            await service.get(actor_for(other), child.page.id)

    async def test_restricted_branch_disappears_from_the_tree(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        """제목만 보여도 정보가 샌다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        public = await service.create(actor, NewPage(space_id=space.id, title="Public"))
        locked = await service.create(actor, NewPage(space_id=space.id, title="Locked"))
        await service.create(
            actor, NewPage(space_id=space.id, title="Inside", parent_id=locked.page.id)
        )
        await service.set_restrictions(
            actor, locked.page.id, mode="view", principals=[("user", user.id)]
        )

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )
        rows = await service.tree(actor_for(other), space.id)
        assert {p.path for p in rows} == {public.page.path}

    async def test_group_membership_satisfies_a_restriction(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(actor, NewPage(space_id=space.id, title="Team only"))

        group = UserGroup(name=f"g-{new_id()}")
        session.add(group)
        await session.flush()
        session.add(GroupMember(group_id=group.id, user_id=other.id))
        await session.flush()

        await service.set_restrictions(
            actor, page.page.id, mode="view", principals=[("group", group.id)]
        )
        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )
        seen = await service.get(actor_for(other, group_ids=frozenset({group.id})), page.page.id)
        assert seen.page.id == page.page.id

    async def test_edit_restriction_blocks_editing_but_not_reading(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(actor, NewPage(space_id=space.id, title="Read only"))
        await service.set_restrictions(
            actor, page.page.id, mode="edit", principals=[("user", user.id)]
        )

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW, perms.PAGE_EDIT),
            scope=Scope.space(space.id),
        )
        # 읽기는 된다.
        assert (await service.get(actor_for(other), page.page.id)).page.id == page.page.id
        with pytest.raises(PermissionDeniedError):
            await service.update(actor_for(other), page.page.id, body="nope")

    async def test_clearing_restrictions_reopens_the_page(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(actor, NewPage(space_id=space.id, title="Toggle"))
        await service.set_restrictions(
            actor, page.page.id, mode="view", principals=[("user", user.id)]
        )
        await service.set_restrictions(actor, page.page.id, mode="view", principals=[])

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )
        assert (await service.get(actor_for(other), page.page.id)).page.id == page.page.id

    async def test_setting_restrictions_needs_the_restrict_permission(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        page = await service.create(actor, NewPage(space_id=space.id, title="A"))

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW, perms.PAGE_EDIT),
            scope=Scope.space(space.id),
        )
        with pytest.raises(PermissionDeniedError):
            await service.set_restrictions(
                actor_for(other), page.page.id, mode="view", principals=[("user", other.id)]
            )


class TestLabels:
    async def test_labels_are_normalized_and_deduped(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        view = await PageService(session, permissions).create(
            actor,
            NewPage(space_id=space.id, title="Doc", labels=["Runbook", "  deploy ", "runbook"]),
        )
        assert view.labels == ["deploy", "runbook"]

    async def test_too_many_labels_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        with pytest.raises(ValidationError) as exc:
            await PageService(session, permissions).create(
                actor,
                NewPage(space_id=space.id, title="Doc", labels=[f"l{i}" for i in range(31)]),
            )
        assert exc.value.code == "wiki.too_many_labels"


class TestSpaceHome:
    async def test_home_page_must_be_in_the_space(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        elsewhere = Space(key="OTHER2", name="Other")
        session.add(elsewhere)
        await session.flush()

        from ieum.modules.wiki.models import Page

        stray = Page(space_id=elsewhere.id, path="x", slug="x", title="X")
        session.add(stray)
        await session.flush()

        with pytest.raises(ValidationError) as exc:
            await SpaceService(session, permissions).update(actor, space.id, home_page_id=stray.id)
        assert exc.value.code == "wiki.home_page_not_in_space"

    async def test_home_page_can_be_set_and_cleared(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        page = await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Home", publish=True)
        )
        spaces = SpaceService(session, permissions)
        updated = await spaces.update(actor, space.id, home_page_id=page.page.id)
        assert updated.home_page_id == page.page.id
        cleared = await spaces.update(actor, space.id, clear_home_page=True)
        assert cleared.home_page_id is None


class TestChangeEvents:
    """문서가 바뀌면 아웃박스에 사건이 남는다.

    남지 않으면 아무에게도 알려지지 않는다 — 그게 M2 까지 이 모듈의 상태였다.
    """

    async def _types(self, session: AsyncSession) -> list[str]:
        await session.flush()
        rows = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "page")
        )
        return [row.event_type for row in rows.scalars().all()]

    async def test_published_page_announces_itself(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Runbook", body="본문", publish=True)
        )
        assert await self._types(session) == ["wiki.page.published"]

    async def test_empty_page_is_not_news_yet(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """만들기는 제목만 받는다. 거기서 알리면 문서 하나에 알림이 둘 간다.

        사람이 한 일은 "문서를 썼다" 하나인데, 화면의 두 단계가 그대로 알림
        두 개가 되면 워처는 곧 알림을 끈다.
        """
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        view = await pages.create(actor, NewPage(space_id=space.id, title="Runbook", publish=True))
        assert await self._types(session) == []

        await pages.update(actor, view.page.id, body="이제 본문이 있다")
        assert await self._types(session) == ["wiki.page.published"]

        await pages.update(actor, view.page.id, body="고쳤다")
        assert await self._types(session) == ["wiki.page.published", "wiki.page.updated"]

    async def test_draft_stays_quiet(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """아직 아무에게도 보이지 않는 글이다. 알리면 초안이 아니다."""
        actor = await full_access(session, user, space)
        await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Draft", body="본문")
        )
        assert await self._types(session) == []

    async def test_publishing_a_draft_is_the_first_news(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """초안이 게시되는 순간이 "새 문서" 다. 수정이라고 하면 거짓말이다."""
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        view = await pages.create(actor, NewPage(space_id=space.id, title="Draft", body="본문"))
        await pages.update(actor, view.page.id, body="고친 본문", publish=True)
        assert await self._types(session) == ["wiki.page.published"]

    async def test_editing_a_published_page_says_updated(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        view = await pages.create(
            actor, NewPage(space_id=space.id, title="Runbook", body="본문", publish=True)
        )
        await pages.update(actor, view.page.id, body="고친 본문", message="오타")
        assert await self._types(session) == ["wiki.page.published", "wiki.page.updated"]

    async def test_payload_carries_the_address(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """notify 는 문서를 되짚어 읽지 않는다. 링크에 필요한 것을 실어 보낸다."""
        actor = await full_access(session, user, space)
        await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Runbook", body="본문", publish=True)
        )
        await session.flush()
        rows = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "page")
        )
        payload = rows.scalars().one().payload
        assert payload["space_key"] == space.key
        assert payload["path"] == "runbook"
        assert payload["title"] == "Runbook"
        assert payload["actor_id"] == str(user.id)

    async def test_restore_counts_as_an_edit(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        view = await pages.create(
            actor, NewPage(space_id=space.id, title="Runbook", body="처음", publish=True)
        )
        await pages.update(actor, view.page.id, body="두 번째")
        await pages.restore(actor, view.page.id, 1)
        assert await self._types(session) == [
            "wiki.page.published",
            "wiki.page.updated",
            "wiki.page.updated",
        ]

    async def test_comment_announces_itself(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.COMMENT_ADD,),
            scope=Scope.space(space.id),
        )
        view = await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Runbook", body="본문", publish=True)
        )
        await PageCommentService(session, permissions).add(actor, view.page.id, body="확인 부탁")
        assert await self._types(session) == ["wiki.page.published", "wiki.page.commented"]

    async def test_mention_must_be_allowed_to_see_the_page(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        """볼 수 없는 사람을 멘션해도 알림 대상이 되면 안 된다.

        notify 는 문서 제한을 못 본다. 거르지 않으면 아무나 멘션해서 제한된
        문서의 제목을 알림으로 흘릴 수 있다.
        """
        actor = await full_access(session, user, space)
        body = f"[@남](user:{other.id}) 확인 부탁"
        await PageService(session, permissions).create(
            actor, NewPage(space_id=space.id, title="Runbook", body=body, publish=True)
        )
        await session.flush()
        rows = await session.execute(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == "page")
        )
        assert rows.scalars().one().payload["mentioned_ids"] == []


class TestBlog:
    """스페이스 블로그 (B13).

    날짜순으로 흐르는 글이라 트리에 안 들어간다. 나머지(버전·코멘트·검색·
    권한)는 문서와 완전히 같아야 한다 — 그래서 별도 표를 만들지 않았다.
    """

    async def test_post_is_not_in_the_tree(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        await pages.create(actor, NewPage(space_id=space.id, title="Runbook", publish=True))
        await pages.create(
            actor, NewPage(space_id=space.id, title="릴리스 노트", kind="blog", publish=True)
        )

        tree = await pages.tree(actor, space.id)
        assert [node.title for node in tree] == ["Runbook"]

    async def test_posts_are_newest_first(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        for title in ("첫 글", "둘째 글", "셋째 글"):
            await pages.create(
                actor, NewPage(space_id=space.id, title=title, kind="blog", publish=True)
            )

        posts, total = await pages.posts(actor, space.id, limit=10, offset=0)
        assert total == 3
        assert [view.page.title for view in posts] == ["셋째 글", "둘째 글", "첫 글"]

    async def test_draft_post_is_not_listed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """쓰다 만 글이 블로그에 뜨면 곤란하다."""
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        await pages.create(actor, NewPage(space_id=space.id, title="초안", kind="blog"))
        posts, total = await pages.posts(actor, space.id, limit=10, offset=0)
        assert posts == [] and total == 0

    async def test_address_does_not_collide_with_a_page(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """같은 이름의 문서와 글이 함께 있을 수 있어야 한다. 주소가 다르다."""
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        page = await pages.create(actor, NewPage(space_id=space.id, title="Runbook", publish=True))
        post = await pages.create(
            actor, NewPage(space_id=space.id, title="Runbook", kind="blog", publish=True)
        )
        assert page.page.path == "runbook"
        assert post.page.path == "blog/runbook"

    async def test_two_posts_with_the_same_title(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        first = await pages.create(
            actor, NewPage(space_id=space.id, title="주간 소식", kind="blog", publish=True)
        )
        second = await pages.create(
            actor, NewPage(space_id=space.id, title="주간 소식", kind="blog", publish=True)
        )
        assert first.page.path != second.page.path

    async def test_unknown_kind_is_rejected(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        with pytest.raises(ValidationError) as exc:
            await PageService(session, permissions).create(
                actor, NewPage(space_id=space.id, title="X", kind="tweet")
            )
        assert exc.value.code == "wiki.page_kind_invalid"

    async def test_post_survives_export_and_import(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """내보낸 묶음을 다시 올리면 글은 글로 돌아와야 한다.

        안 그러면 내보내기 한 번에 블로그가 통째로 트리 문서가 된다
        (wiki-markdown.md 8절 라운드트립 계약).
        """
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions)
        await pages.create(
            actor,
            NewPage(
                space_id=space.id,
                title="릴리스 노트",
                body="새 판이 나왔다",
                kind="blog",
                publish=True,
            ),
        )
        archive = await pages.export_space(actor, space.id)

        other = Space(key=f"T{new_id().hex[:6].upper()}", name="Copy")
        session.add(other)
        await session.flush()
        target = await full_access(session, user, other)
        restored = await pages.import_archive(target, space_id=other.id, data=archive)

        assert [view.page.kind for view in restored] == ["blog"]
        assert restored[0].page.path == "blog/릴리스-노트"

    async def test_a_post_can_be_commented_like_any_page(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
    ) -> None:
        """별도 표를 만들지 않은 이유가 이것이다."""
        actor = await full_access(session, user, space)
        await grant(
            session,
            principal_id=user.id,
            permissions_granted=(perms.COMMENT_ADD,),
            scope=Scope.space(space.id),
        )
        post = await PageService(session, permissions).create(
            actor,
            NewPage(space_id=space.id, title="공지", body="읽어 주세요", kind="blog", publish=True),
        )
        comment = await PageCommentService(session, permissions).add(
            actor, post.page.id, body="확인했습니다"
        )
        assert comment.comment.page_id == post.page.id


@pytest.fixture
def page_attachments(permissions: PermissionService) -> None:
    """문서 첨부의 권한 리졸버. main 이 기동할 때 하는 일과 같다.

    리졸버는 전역 권한 서비스를 본다(core 는 어느 모듈이 첨부를 쓰는지 모른다).
    라우터 없이 서비스만 쓰는 테스트라 여기서 직접 꽂는다.
    """
    wiki_attachments.install()
    set_permission_service(permissions)


class TestImportedAssets:
    """ZIP 안의 그림을 첨부로 흡수한다.

    안 하면 `![그림](images/a.png)` 이 통째로 깨진 링크가 된다 — 올린 사람은
    ZIP 에 그림을 같이 넣었는데도.
    """

    @staticmethod
    def _zip(files: dict[str, bytes]) -> bytes:
        import io
        import zipfile

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for path, body in files.items():
                archive.writestr(path, body)
        return buffer.getvalue()

    async def _attachments(self, session: AsyncSession, page_id: UUID) -> list[Attachment]:
        rows = await session.execute(select(Attachment).where(Attachment.owner_id == page_id))
        return list(rows.scalars().all())

    async def test_relative_image_becomes_an_attachment(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        actor = await full_access(session, user, space)
        data = self._zip(
            {
                "docs/guide.md": "# 안내\n\n![그림](images/a.png)\n".encode(),
                "docs/images/a.png": b"\x89PNG\r\n\x1a\n" + b"0" * 32,
            }
        )
        views = await PageService(session, permissions, store=ready_store).import_archive(
            actor, space_id=space.id, data=data
        )
        page = views[0].page

        rows = await self._attachments(session, page.id)
        assert [row.filename for row in rows] == ["a.png"]
        assert rows[0].mime == "image/png"

        fresh = await PageService(session, permissions).get(actor, page.id)
        assert f"attachment:{rows[0].id}/a.png" in fresh.body
        assert "images/a.png" not in fresh.body

    async def test_only_referenced_files_are_absorbed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """묶음에 딸려 온 `.DS_Store` 까지 첨부가 되면 안 된다."""
        actor = await full_access(session, user, space)
        data = self._zip(
            {
                "guide.md": "![그림](a.png)".encode(),
                "a.png": b"\x89PNG" + b"0" * 16,
                "unused.png": b"\x89PNG" + b"0" * 16,
                ".DS_Store": b"junk",
            }
        )
        views = await PageService(session, permissions, store=ready_store).import_archive(
            actor, space_id=space.id, data=data
        )
        rows = await self._attachments(session, views[0].page.id)
        assert [row.filename for row in rows] == ["a.png"]

    async def test_missing_file_is_left_alone(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """조용히 지우면 무엇이 있었는지도 사라진다. 깨진 링크가 낫다."""
        actor = await full_access(session, user, space)
        data = self._zip({"guide.md": "![없는 그림](images/gone.png)".encode()})
        views = await PageService(session, permissions, store=ready_store).import_archive(
            actor, space_id=space.id, data=data
        )
        assert "images/gone.png" in views[0].body
        assert await self._attachments(session, views[0].page.id) == []

    async def test_absorption_makes_no_second_version(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """올리자마자 이력이 두 줄이면, 첫 줄은 아무도 못 본 깨진 판이다."""
        actor = await full_access(session, user, space)
        data = self._zip({"guide.md": "![그림](a.png)".encode(), "a.png": b"\x89PNG" + b"0" * 16})
        views = await PageService(session, permissions, store=ready_store).import_archive(
            actor, space_id=space.id, data=data
        )
        history = await PageService(session, permissions).history(actor, views[0].page.id)
        assert len(history) == 1

    async def test_without_a_store_the_link_stays(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        page_attachments: None,
    ) -> None:
        """스토리지가 없으면 손대지 않는다. 링크를 지우는 것보다 낫다."""
        actor = await full_access(session, user, space)
        data = self._zip({"guide.md": "![그림](a.png)".encode(), "a.png": b"\x89PNG" + b"0" * 16})
        views = await PageService(session, permissions).import_archive(
            actor, space_id=space.id, data=data
        )
        assert "a.png" in views[0].body


class TestExportedAssets:
    """내보낸 묶음에 첨부가 함께 담긴다.

    안 담으면 내보내기가 이 서버에 묶인다 — 옮긴 쪽에서 그림이 전부 깨진 채로.
    담고 나면 상대 경로가 되고, 다시 올릴 때 임포트가 도로 흡수한다(왕복).
    """

    @staticmethod
    def _members(archive: bytes) -> dict[str, bytes]:
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            return {name: zf.read(name) for name in zf.namelist()}

    @staticmethod
    def _names(archive: bytes) -> list[str]:
        """중복까지 보이는 목록. dict 로 접으면 같은 이름 둘이 하나로 보인다."""
        import io
        import zipfile

        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            return zf.namelist()

    async def _page_with_image(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        actor: Actor,
        space: Space,
        store: ObjectStore,
        *,
        title: str = "안내",
        data: bytes = b"\x89PNG\r\n\x1a\n" + b"0" * 32,
    ) -> Attachment:
        pages = PageService(session, permissions, store=store)
        view = await pages.create(actor, NewPage(space_id=space.id, title=title, publish=True))
        row = await AttachmentService(session, store).ingest(
            actor,
            owner_type=wiki_attachments.OWNER_PAGE,
            owner_id=view.page.id,
            filename="a.png",
            mime="image/png",
            data=data,
        )
        await pages.update(actor, view.page.id, body=f"![그림](attachment:{row.id}/a.png)")
        return row

    async def test_attachment_travels_with_the_document(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        actor = await full_access(session, user, space)
        blob = b"\x89PNG\r\n\x1a\n" + b"pixels"
        row = await self._page_with_image(
            session, permissions, actor, space, ready_store, data=blob
        )

        archive = await PageService(session, permissions, store=ready_store).export_space(
            actor, space.id
        )
        members = self._members(archive)

        # 문서 옆 폴더에 둔다. id 를 한 겹 끼워 같은 이름끼리 덮어쓰지 않게.
        assert members[f"안내.assets/{row.id}/a.png"] == blob
        # 본문은 상대 경로로 돌아온다 — 서명된 주소도, 스킴도 아니다.
        assert f"](안내.assets/{row.id}/a.png)" in members["안내.md"].decode()
        assert "attachment:" not in members["안내.md"].decode()

    async def test_nested_document_keeps_the_link_relative(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """`docs/guide.md` 의 첨부는 `docs/guide.assets/…` 에. 상대 경로는
        `guide.assets/…` 여야 한다 — 문서 위치를 기준으로 풀리기 때문이다."""
        actor = await full_access(session, user, space)
        pages = PageService(session, permissions, store=ready_store)
        parent = await pages.create(actor, NewPage(space_id=space.id, title="docs", publish=True))
        child = await pages.create(
            actor,
            NewPage(space_id=space.id, title="guide", parent_id=parent.page.id, publish=True),
        )
        row = await AttachmentService(session, ready_store).ingest(
            actor,
            owner_type=wiki_attachments.OWNER_PAGE,
            owner_id=child.page.id,
            filename="a.png",
            mime="image/png",
            data=b"\x89PNG" + b"0" * 16,
        )
        await pages.update(actor, child.page.id, body=f"![그림](attachment:{row.id}/a.png)")

        members = self._members(await pages.export_space(actor, space.id))
        assert f"docs/guide.assets/{row.id}/a.png" in members
        assert f"](guide.assets/{row.id}/a.png)" in members["docs/guide.md"].decode()

    async def test_export_then_import_restores_the_attachment(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """왕복이 닫힌다. 여기가 안 닫히면 옮길 때마다 그림을 잃는다."""
        actor = await full_access(session, user, space)
        blob = b"\x89PNG\r\n\x1a\n" + b"round-trip"
        row = await self._page_with_image(
            session, permissions, actor, space, ready_store, data=blob
        )
        pages = PageService(session, permissions, store=ready_store)
        archive = await pages.export_space(actor, space.id)

        other = Space(key=f"T{new_id().hex[:6].upper()}", name="Copy")
        session.add(other)
        await session.flush()
        target = await full_access(session, user, other)
        restored = await pages.import_archive(target, space_id=other.id, data=archive)

        rows = (
            (
                await session.execute(
                    select(Attachment).where(Attachment.owner_id == restored[0].page.id)
                )
            )
            .scalars()
            .all()
        )
        assert [r.filename for r in rows] == ["a.png"]
        assert rows[0].id != row.id
        assert await ready_store.get(rows[0].storage_key) == blob
        assert f"attachment:{rows[0].id}/a.png" in restored[0].body

    async def test_oversized_attachment_is_skipped_and_listed(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """조용히 빠지면 옮긴 쪽에서 영영 모른다. 본문도 손대지 않는다 —
        주소만 바꿔 놓고 파일을 안 담으면 깨진 링크가 된다."""
        monkeypatch.setattr(service_module, "MAX_EXPORT_ASSET_BYTES", 8)
        actor = await full_access(session, user, space)
        row = await self._page_with_image(
            session, permissions, actor, space, ready_store, data=b"\x89PNG" + b"0" * 64
        )

        members = self._members(
            await PageService(session, permissions, store=ready_store).export_space(actor, space.id)
        )
        assert not any(name.endswith("a.png") for name in members)
        manifest = members[SKIPPED_MANIFEST].decode()
        assert f"attachment:{row.id}/a.png" in manifest
        assert "안내.md" in manifest
        assert f"attachment:{row.id}/a.png" in members["안내.md"].decode()

    async def test_nothing_is_skipped_when_everything_fits(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        actor = await full_access(session, user, space)
        await self._page_with_image(session, permissions, actor, space, ready_store)
        members = self._members(
            await PageService(session, permissions, store=ready_store).export_space(actor, space.id)
        )
        assert SKIPPED_MANIFEST not in members

    async def test_a_deleted_attachment_leaves_the_body_alone(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """읽을 수 없는 첨부의 주소를 바꾸면 무엇이 있었는지도 사라진다."""
        actor = await full_access(session, user, space)
        row = await self._page_with_image(session, permissions, actor, space, ready_store)
        await AttachmentService(session, ready_store).delete(actor, row.id)

        members = self._members(
            await PageService(session, permissions, store=ready_store).export_space(actor, space.id)
        )
        assert f"attachment:{row.id}/a.png" in members["안내.md"].decode()
        assert not any(".assets/" in name for name in members)

    async def test_one_attachment_two_references_is_packed_once(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """같은 첨부를 두 표기로 가리킬 수 있다. 두 번 담으면 ZIP 에 같은
        이름이 두 개 들어가고 상한도 두 배로 깎인다."""
        actor = await full_access(session, user, space)
        row = await self._page_with_image(session, permissions, actor, space, ready_store)
        pages = PageService(session, permissions, store=ready_store)
        page = (await pages.get_by_path(actor, space.key, "안내")).page
        await pages.update(
            actor,
            page.id,
            body=f"![1](attachment:{row.id}/a.png) [2](attachment:{row.id})",
        )

        archive = await pages.export_space(actor, space.id)
        assert [n for n in self._names(archive) if n.endswith("a.png")] == [
            f"안내.assets/{row.id}/a.png"
        ]
        body = self._members(archive)["안내.md"].decode()
        assert body.count(f"안내.assets/{row.id}/a.png") == 2

    async def test_without_a_store_the_scheme_stays(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        space: Space,
        ready_store: ObjectStore,
        page_attachments: None,
    ) -> None:
        """스토리지가 없으면 손대지 않는다. 링크를 지우는 것보다 낫다."""
        actor = await full_access(session, user, space)
        row = await self._page_with_image(session, permissions, actor, space, ready_store)
        members = self._members(
            await PageService(session, permissions).export_space(actor, space.id)
        )
        assert f"attachment:{row.id}/a.png" in members["안내.md"].decode()


class TestCopyKeepsRestrictions:
    """복사본이 제한을 잃으면 안 된다.

    제한이 걸린 문서를 복사하면 새 문서에는 제한이 없다. 복사한 사람은 원래
    볼 수 있던 사람이니 권한 상승은 아니지만, **누구나 볼 수 있는 사본이
    조용히 생긴다** — 가린 의미가 사라진다.
    """

    async def test_a_restricted_page_stays_restricted_when_copied(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        secret = await service.create(actor, NewPage(space_id=space.id, title="Secret"))
        await service.set_restrictions(
            actor, secret.page.id, mode="view", principals=[("user", user.id)]
        )

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )

        # 최상위로 복사한다 — 물려받을 상위 제한이 없는 자리다.
        copied = await service.copy(actor, secret.page.id, new_parent_id=None)
        with pytest.raises(PermissionDeniedError):
            await service.get(actor_for(other), copied.page.id)

    async def test_children_keep_their_own_restrictions_too(
        self,
        session: AsyncSession,
        permissions: PermissionService,
        user: User,
        other: User,
        space: Space,
    ) -> None:
        """가지째 복사한다. 아래쪽 문서에 따로 걸린 제한도 따라가야 한다."""
        actor = await full_access(session, user, space)
        service = PageService(session, permissions)
        top = await service.create(actor, NewPage(space_id=space.id, title="Handbook"))
        inner = await service.create(
            actor, NewPage(space_id=space.id, title="Salaries", parent_id=top.page.id)
        )
        await service.set_restrictions(
            actor, inner.page.id, mode="view", principals=[("user", user.id)]
        )

        await grant(
            session,
            principal_id=other.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )

        copied = await service.copy(actor, top.page.id, new_parent_id=None)
        tree = await service.tree(actor, space.id)
        child = next(n for n in tree if n.path == f"{copied.page.path}/salaries")
        with pytest.raises(PermissionDeniedError):
            await service.get(actor_for(other), child.id)
