"""문서의 태스크 — 유도 표, 체크, 집계 (feature-map B12).

파서는 `test_markdown_tasks.py` 가 값으로 붙잡는다. 여기서 보는 것은 **그
파서가 저장 경로와 권한에 제대로 이어졌는가**이고, 틀리면 조용히 틀린다:

- **유도 표는 저장할 때 다시 만들어진다.** 안 그러면 본문을 고쳤는데 "내 할
  일" 에는 옛 항목이 남고, 그건 새로고침해도 안 사라진다.
- **초안·아카이브의 태스크는 아무에게도 안 보인다.** 아직 안 낸 계획이
  남의 목록에 뜨는 것은 내용 유출이다.
- **체크는 그 줄만 바꾸고 판을 하나 남긴다.** 이력에 안 남기면 "누가 언제
  이걸 끝냈다고 했나" 를 답할 수 없다.
- **줄이 움직였으면 거절한다.** 그대로 진행하면 엉뚱한 줄이 체크된다.
- **집계는 볼 수 있는 문서만 준다.** 못 보는 문서의 할 일이 목록에 뜨면 제목
  으로 내용이 샌다.
- **없는 계정이 담당자로 적혀도 문서는 저장된다.** 태스크 한 줄 때문에 문서
  저장이 실패하면 안 된다.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.exceptions import ConflictError, PermissionDeniedError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.identity.models import User
from ieum.modules.org.repository import OrgPermissionResolver
from ieum.modules.wiki import collab
from ieum.modules.wiki import permissions as perms
from ieum.modules.wiki.models import Page, PageCollab, PageTask, PageVersion, Space
from ieum.modules.wiki.service import NewPage, PageRestrictionGuard, PageService
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    """**문서 제한 관문까지 끼운다.**

    `main.py` 가 실제로 그렇게 등록한다(`register_guard`). 관문 없이 시험하면
    "스코프 권한만 보는 코드" 가 통과하고, 제한된 문서의 할 일이 새는 것을
    아무도 못 본다 — 실제로 이 시험을 처음 썼을 때 관문을 안 끼워 붉었고,
    그 붉음이 제품 결함처럼 보였다.
    """
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Page, PageRestrictionGuard())
    set_permission_service(service)
    return service


async def _space(session: AsyncSession, name: str = "Docs") -> Space:
    row = Space(key=f"T{new_id().hex[-6:].upper()}", name=name)
    session.add(row)
    await session.flush()
    return row


async def _editor(session: AsyncSession, space: Space) -> User:
    row = User(email=f"e-{new_id()}@example.com", display_name="편집자", status="active")
    session.add(row)
    await session.flush()
    await grant(
        session,
        principal_id=row.id,
        permissions_granted=(perms.PAGE_VIEW, perms.PAGE_EDIT, perms.PAGE_CREATE),
        scope=Scope.space(space.id),
    )
    return row


async def _page(
    session: AsyncSession,
    permissions: PermissionService,
    space: Space,
    author: User,
    body: str,
    *,
    publish: bool = True,
) -> object:
    service = PageService(session, permissions)
    view = await service.create(
        actor_for(author),
        NewPage(space_id=space.id, title=f"문서 {new_id().hex[-4:]}", body=body, publish=publish),
    )
    return view.page


def _mention(user: User) -> str:
    return f"[@{user.display_name}](user:{user.id})"


class TestTheDerivedTable:
    async def test_saving_a_page_fills_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        page = await _page(session, permissions, space, author, "- [ ] 첫 일\n- [x] 끝난 일\n")

        rows = await PageService(session, permissions).list_tasks(actor_for(author), page.id)  # type: ignore[attr-defined]
        assert [(r.line, r.done, r.text) for r in rows] == [
            (0, False, "첫 일"),
            (1, True, "끝난 일"),
        ]

    async def test_editing_the_body_rebuilds_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**옛 항목이 남으면 새로고침해도 안 사라진다.**

        한 줄씩 갱신하려 들면 본문이 밀릴 때 유령 태스크가 남는다. 통째로
        다시 만드는 것이 그 문제를 아예 없앤다.
        """
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 지울 일\n")

        await service.update(actor_for(author), page.id, body="- [ ] 남길 일\n")  # type: ignore[attr-defined]

        rows = await service.list_tasks(actor_for(author), page.id)  # type: ignore[attr-defined]
        assert [r.text for r in rows] == ["남길 일"]

    async def test_a_draft_gets_no_task_rows(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**초안은 유도 표에 아예 안 들어간다.**

        아직 안 낸 계획이 남의 "내 할 일" 에 뜨면 내용 유출이다. 검색 색인이
        초안을 빼는 것과 같은 이유이고, 같은 자리에서 처리한다.

        이 시험이 붙잡는 것은 "안 넣는다" 다 — 게시된 뒤 초안으로 돌아가는
        길은 오늘 없으므로(게시는 한 방향), 그 경우의 지우기는 방어일 뿐이고
        여기서 확인되지 않는다. `_rederive` 주석에 그렇게 적었다.
        """
        space = await _space(session)
        author = await _editor(session, space)
        page = await _page(session, permissions, space, author, "- [ ] 아직 비밀\n", publish=False)

        rows = await PageService(session, permissions).list_tasks(actor_for(author), page.id)  # type: ignore[attr-defined]
        assert rows == []

    async def test_archiving_clears_them(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        await grant(
            session,
            principal_id=author.id,
            permissions_granted=(perms.PAGE_DELETE,),
            scope=Scope.space(space.id),
        )
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 접힐 일\n")

        await service.archive(actor_for(author), page.id)  # type: ignore[attr-defined]

        left = await session.scalar(
            select(func.count()).select_from(PageTask).where(PageTask.page_id == page.id)  # type: ignore[attr-defined]
        )
        assert left == 0

    async def test_it_reads_the_assignee_and_due_date(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        page = await _page(
            session,
            permissions,
            space,
            author,
            f"- [ ] 배포 문서 정리 {_mention(author)} due:2026-09-30\n",
        )

        row = (await PageService(session, permissions).list_tasks(actor_for(author), page.id))[0]  # type: ignore[attr-defined]
        assert row.assignee_id == author.id
        assert row.due_date == date(2026, 9, 30)

    async def test_an_unknown_assignee_does_not_break_the_save(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**태스크 한 줄 때문에 문서 저장이 실패하면 안 된다.**

        `assignee_id` 는 FK 다. 없는 사용자를 넣으면 저장 자체가 터지는데,
        본문에는 사람이 손으로 아무 UUID 나 적을 수 있다.
        """
        space = await _space(session)
        author = await _editor(session, space)
        ghost = uuid4()
        page = await _page(
            session, permissions, space, author, f"- [ ] 유령에게 [@X](user:{ghost})\n"
        )

        row = (await PageService(session, permissions).list_tasks(actor_for(author), page.id))[0]  # type: ignore[attr-defined]
        assert row.assignee_id is None, "없는 계정이 담당자로 저장됐다"
        assert "유령에게" in row.text


class TestChecking:
    async def test_it_flips_the_body_and_makes_a_version(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 할 일\n- [ ] 다른 일\n")
        before = await session.scalar(
            select(func.count()).select_from(PageVersion).where(PageVersion.page_id == page.id)  # type: ignore[attr-defined]
        )

        view = await service.set_task_done(actor_for(author), page.id, line=0, done=True)  # type: ignore[attr-defined]

        # 정규화가 끝의 빈 줄을 떼므로 마지막 줄바꿈은 없다 — 그것이
        # 이 파이프라인의 약속이다(`markdown.normalize`).
        assert view.body == "- [x] 할 일\n- [ ] 다른 일"
        after = await session.scalar(
            select(func.count()).select_from(PageVersion).where(PageVersion.page_id == page.id)  # type: ignore[attr-defined]
        )
        # **이력에 남는다.** 안 남기면 누가 언제 끝냈다고 했는지 못 답한다.
        assert after == (before or 0) + 1

    async def test_the_derived_row_follows(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 할 일\n")

        await service.set_task_done(actor_for(author), page.id, line=0, done=True)  # type: ignore[attr-defined]

        rows = await service.list_tasks(actor_for(author), page.id)  # type: ignore[attr-defined]
        assert rows[0].done is True

    async def test_the_shared_editing_room_follows(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**같이 편집하는 방의 상태도 따라가야 한다.**

        방은 CRDT 상태를 따로 들고 있다(B16). 체크가 그 상태를 안 고치면 방이
        낡은 본문을 든 두 번째 사본이 되고, 다음에 편집기를 여는 사람은 체크
        안 된 본문을 본다 — 그대로 저장하면 체크가 조용히 되돌아간다. 편집기
        가 든 판 번호는 최신이므로 낙관적 잠금도 그걸 막지 못한다.

        실제로 이 순서를 브라우저에서 밟았을 때 그렇게 됐고, 그것을 잡아
        `_rederive` 가 방을 맞추게 됐다.
        """
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 눌러 볼 일\n")
        # 누군가 편집기를 한 번 열었다 — 그때 방이 생긴다.
        await collab.load_or_seed(session, page.id)  # type: ignore[attr-defined]

        await service.set_task_done(actor_for(author), page.id, line=0, done=True)  # type: ignore[attr-defined]

        row = (
            await session.execute(
                select(PageCollab).where(PageCollab.page_id == page.id)  # type: ignore[attr-defined]
            )
        ).scalar_one()
        assert collab.text_of(row.state) == "- [x] 눌러 볼 일"

    async def test_the_editors_own_save_leaves_the_room_alone(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**화면의 저장은 방을 건드리지 않는다** — 건드리면 글이 두 벌이 된다.

        저장이 올리는 본문은 **방의 글 그대로**다. 그것을 "밖에서 생긴 변경"
        으로 방에 다시 넣으면 같은 글이 두 벌이 된다. 스냅샷이 살아 있는 방보다
        늦기 때문에 "저장된 상태와 다르다" 로는 구분할 수 없고 — 실제로 그렇게
        했을 때 `'본문이다.'` 가 `'본문이다.본문이다.'` 가 됐다.

        그래서 방을 맞추는 것은 **방을 거칠 수 없는 경로**만 한다
        (`update(align_room=True)`). 이 시험은 그 기본값을 붙잡는다.
        """
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "처음\n")
        seeded = await collab.load_or_seed(session, page.id)  # type: ignore[attr-defined]

        await service.update(actor_for(author), page.id, body="방에서 친 글\n")  # type: ignore[attr-defined]

        row = (
            await session.execute(
                select(PageCollab).where(PageCollab.page_id == page.id)  # type: ignore[attr-defined]
            )
        ).scalar_one()
        assert collab.text_of(row.state) == collab.text_of(seeded)

    async def test_checking_what_is_already_checked_makes_no_version(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """두 사람이 같은 것을 체크했을 때 이력이 같은 내용으로 두 줄 쌓이는
        것을 막는다."""
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [x] 이미 끝남\n")
        before = await session.scalar(
            select(func.count()).select_from(PageVersion).where(PageVersion.page_id == page.id)  # type: ignore[attr-defined]
        )

        await service.set_task_done(actor_for(author), page.id, line=0, done=True)  # type: ignore[attr-defined]

        after = await session.scalar(
            select(func.count()).select_from(PageVersion).where(PageVersion.page_id == page.id)  # type: ignore[attr-defined]
        )
        assert after == before

    async def test_a_line_that_moved_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**엉뚱한 줄을 체크하지 않는다.**

        화면이 문서를 읽은 뒤 남이 줄을 지웠으면, 그 줄 번호는 이제 다른
        것을 가리킨다. 조용히 진행하면 사람은 자기가 안 만진 항목이 체크된
        것을 나중에 발견한다.
        """
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 하나\n- [ ] 둘\n")
        await service.update(actor_for(author), page.id, body="그냥 글로 바꿨다\n")  # type: ignore[attr-defined]

        with pytest.raises(ConflictError) as exc:
            await service.set_task_done(actor_for(author), page.id, line=0, done=True)  # type: ignore[attr-defined]
        assert exc.value.code == "wiki.task_line_moved"

    async def test_changed_text_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """판 번호만으로는 부족하다: 판이 같아도 화면이 캐시된 옛 본문을 보고
        있었으면 줄이 어긋난다. 화면이 본 글을 함께 받아 맞춘다."""
        space = await _space(session)
        author = await _editor(session, space)
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, author, "- [ ] 지금 글\n")

        with pytest.raises(ConflictError) as exc:
            await service.set_task_done(
                actor_for(author),
                page.id,  # type: ignore[attr-defined]
                line=0,
                done=True,
                expect_text="옛 글",
            )
        assert exc.value.code == "wiki.task_text_changed"

    async def test_a_viewer_cannot_check(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        page = await _page(session, permissions, space, author, "- [ ] 남의 일\n")
        viewer = User(email=f"v-{new_id()}@example.com", display_name="구경꾼", status="active")
        session.add(viewer)
        await session.flush()
        await grant(
            session,
            principal_id=viewer.id,
            permissions_granted=(perms.PAGE_VIEW,),
            scope=Scope.space(space.id),
        )

        with pytest.raises(PermissionDeniedError):
            await PageService(session, permissions).set_task_done(
                actor_for(viewer),
                page.id,  # type: ignore[attr-defined]
                line=0,
                done=True,
            )


class TestMyTasks:
    async def test_it_gathers_across_pages(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        await _page(session, permissions, space, author, f"- [ ] 첫 문서 일 {_mention(author)}\n")
        await _page(session, permissions, space, author, f"- [ ] 둘째 문서 일 {_mention(author)}\n")

        rows = await PageService(session, permissions).my_tasks(actor_for(author))
        assert {r.task.text.split(" ")[0] for r in rows} == {"첫", "둘째"}
        # **어느 문서의 일인지 함께 온다.** 없으면 하나씩 눌러 확인해야 한다.
        assert all(r.page_title for r in rows)
        assert all(r.space_key == space.key for r in rows)

    async def test_done_ones_are_hidden_by_default(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        await _page(
            session,
            permissions,
            space,
            author,
            f"- [x] 끝난 일 {_mention(author)}\n- [ ] 남은 일 {_mention(author)}\n",
        )

        service = PageService(session, permissions)
        assert [r.task.done for r in await service.my_tasks(actor_for(author))] == [False]
        assert len(await service.my_tasks(actor_for(author), include_done=True)) == 2

    async def test_someone_elses_task_is_not_mine(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        space = await _space(session)
        author = await _editor(session, space)
        other = await _editor(session, space)
        await _page(session, permissions, space, author, f"- [ ] 남의 일 {_mention(other)}\n")

        assert await PageService(session, permissions).my_tasks(actor_for(author)) == []

    async def test_a_page_i_cannot_see_is_left_out(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**제목으로 내용이 새지 않게 한다.**

        집계는 문서를 가로지르므로, 스페이스 권한을 안 보면 다른 팀 문서의
        할 일이 그대로 목록에 뜬다 — 그 제목만으로도 새는 것이 있다.
        """
        mine = await _space(session, "내 스페이스")
        theirs = await _space(session, "남의 스페이스")
        me = await _editor(session, mine)
        them = await _editor(session, theirs)
        # 남의 스페이스 문서에서 **나를** 담당자로 걸었다.
        await _page(session, permissions, theirs, them, f"- [ ] 몰래 시킨 일 {_mention(me)}\n")

        assert await PageService(session, permissions).my_tasks(actor_for(me)) == []

    async def test_a_restricted_page_is_left_out(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**문서 단위 제한도 걸러야 한다.**

        스페이스 권한만 보면, 같은 스페이스 안에서 제한이 걸린 문서의 할 일이
        그대로 목록에 뜬다 — 제목만으로도 새는 것이 있다. 제한은 조상에서
        상속되므로 SQL 로 다시 구현하면 규칙이 두 벌이 된다. 그래서 SQL 은
        스페이스까지만 좁히고, 문서 제한은 한 줄씩 `PAGE_VIEW` 를 물어 거른다.

        이 시험이 없으면 그 한 줄씩 검사를 지워도 아무것도 붉어지지 않는다
        (되돌려 보고 알았다).
        """
        space = await _space(session)
        me = await _editor(session, space)
        owner = await _editor(session, space)
        await grant(
            session,
            principal_id=owner.id,
            permissions_granted=(perms.PAGE_RESTRICT,),
            scope=Scope.space(space.id),
        )
        service = PageService(session, permissions)
        page = await _page(session, permissions, space, owner, f"- [ ] 비밀 일 {_mention(me)}\n")

        # 주인만 볼 수 있게 잠근다. 나는 담당자인데도 문서를 못 본다.
        await service.set_restrictions(
            actor_for(owner),
            page.id,  # type: ignore[attr-defined]
            mode="view",
            principals=[("user", owner.id)],
        )

        assert await service.my_tasks(actor_for(me)) == [], "제한된 문서의 할 일이 샜다"

    async def test_the_limit_counts_only_what_i_can_see(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**상한은 볼 수 있는 것에 걸려야 한다.**

        걸러내기를 SQL 이 아니라 파이썬에서만 하면, 못 보는 태스크가 상한을
        먼저 채우고 내 것은 밀려난다 — 그러면 목록이 **빈 채로** 온다. 있는데
        안 보이는 것이고, 화면에는 "할 일이 없다" 로 뜬다.

        `test_a_page_i_cannot_see_is_left_out` 로는 이걸 못 잡는다: 그 시험은
        서비스의 한 줄씩 검사만으로도 통과하기 때문이다(되돌려 보고 알았다).
        """
        mine = await _space(session, "내 스페이스")
        theirs = await _space(session, "남의 스페이스")
        me = await _editor(session, mine)
        them = await _editor(session, theirs)
        # 남의 문서 것이 **먼저 정렬된다**(기한이 이르다).
        await _page(
            session, permissions, theirs, them, f"- [ ] 못 보는 일 {_mention(me)} due:2026-01-01\n"
        )
        await _page(session, permissions, mine, me, f"- [ ] 내 일 {_mention(me)} due:2026-06-01\n")

        rows = await PageService(session, permissions).my_tasks(actor_for(me), limit=1)
        assert [r.task.text.split(" ")[0] for r in rows] == ["내"], "상한이 못 보는 것에 먹혔다"

    async def test_the_ones_with_a_due_date_come_first(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """기한 있는 것이 먼저 급하다. 기한 없는 것을 앞에 두면 목록의 첫
        줄이 가장 안 급한 일이 된다."""
        space = await _space(session)
        author = await _editor(session, space)
        await _page(
            session,
            permissions,
            space,
            author,
            f"- [ ] 기한 없음 {_mention(author)}\n"
            f"- [ ] 늦은 기한 {_mention(author)} due:2026-12-31\n"
            f"- [ ] 이른 기한 {_mention(author)} due:2026-01-31\n",
        )

        rows = await PageService(session, permissions).my_tasks(actor_for(author))
        assert [r.task.due_date for r in rows] == [
            date(2026, 1, 31),
            date(2026, 12, 31),
            None,
        ]
