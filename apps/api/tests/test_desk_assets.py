"""자산·구성 항목과 티켓의 연결 (feature-map C15). 실제 Postgres 를 쓴다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **자산은 지워지지 않는다.** 지난 티켓이 그 자산을 가리키고 있고, 그 이력이
  이 기능의 값이다. 링크가 있는 자산의 DELETE 는 거절한다.
- **자산번호는 대문자로 하나다.** 사람이 손으로 치는 값이라 `A-1024` 와
  `a-1024` 가 두 자산이 되면 재고를 못 믿는다.
- **목록은 검색이다.** 상한 없는 전체 목록을 주는 길이 없어야 한다 — 이
  저장소는 그 실수를 세 번 했다.
- **나간 장비는 후보에 안 섞인다.** 티켓에 이을 자산을 고를 때 `retired` 가
  보이면 잘못 고른다. 재고 화면은 켜서 본다.
- **두 문을 다 지난다.** 이으려면 티켓을 고칠 수 있어야 하고 자산을 볼 수
  있어야 한다. 떼는 것은 티켓만으로 된다 — 자산을 못 보게 된 사람이 자기
  티켓의 잘못된 링크를 못 떼면 그 티켓은 틀린 채로 남는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from uuid import uuid4

import pytest
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
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.core.time import utcnow
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.assets import (
    AssetService,
    AssetTypeService,
    NewAsset,
    clean_status,
    clean_tag,
)
from ieum.modules.desk.models import Asset, AssetType, CustomerOrganization
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.issues.service import SecurityLevelGuard
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

# ── 값으로 붙잡는 것 ────────────────────────────────────────────


class TestTag:
    def test_a_tag_is_stored_in_upper_case(self) -> None:
        """`A-1024` 와 `a-1024` 가 두 자산이 되면 재고를 못 믿는다."""
        assert clean_tag("a-1024") == "A-1024"
        assert clean_tag("  a-1024  ") == "A-1024"

    def test_an_empty_tag_is_nothing(self) -> None:
        """교실과 서비스에는 자산번호가 없다. 빈 문자열을 저장하면 그 값 하나로
        유니크가 막혀 두 번째 자산을 못 만든다."""
        assert clean_tag(None) is None
        assert clean_tag("") is None
        assert clean_tag("   ") is None

    def test_a_tag_with_spaces_or_hangul_is_refused(self) -> None:
        """바코드에서 읽고 손으로 치는 값이다. 넓게 받으면 같은 번호가 두
        자산이 된다."""
        for bad in ("A 1024", "노트북-1", "A#1024", "-A1024"):
            with pytest.raises(ValidationError) as caught:
                clean_tag(bad)
            assert caught.value.code == "desk.invalid_asset_tag"

    def test_a_plain_tag_survives(self) -> None:
        assert clean_tag("SN/2024.001-A") == "SN/2024.001-A"


class TestStatus:
    def test_the_default_is_in_use(self) -> None:
        assert clean_status(None) == "in_use"

    def test_an_unknown_status_is_refused(self) -> None:
        """상태는 고정 어휘다. 설치마다 다르면 "수리 중 몇 대" 를 비교할 수
        없고, 그러면 이 열은 자유 메모와 같아진다."""
        with pytest.raises(ValidationError) as caught:
            clean_status("망가짐")
        assert caught.value.code == "desk.invalid_asset_status"


# ── 실제 DB ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    set_permission_service(service)
    return service


async def _user(session: AsyncSession) -> User:
    row = User(
        email=f"u-{new_id()}@example.com",
        display_name=f"사람 {new_id().hex[-6:]}",
        status="active",
    )
    session.add(row)
    await session.flush()
    return row


async def _actor(
    session: AsyncSession,
    *,
    global_grants: Sequence[str] = (),
    project: Project | None = None,
    project_grants: Sequence[str] = (),
) -> Actor:
    """권한을 정확히 준 사람. 전역과 프로젝트를 따로 받는다 — 자산은 전역이고
    티켓은 프로젝트라, 이 기능에서 그 둘이 만난다."""
    user = await _user(session)
    repo = RoleRepository(session)
    if global_grants:
        role = Role(name=f"g-{new_id().hex[-8:]}", scope_kind="global")
        repo.add(role)
        await session.flush()
        for permission in global_grants:
            repo.grant(role.id, permission)
        repo.assign(
            role_id=role.id,
            scope=Scope.global_(),
            principal_kind="user",
            principal_id=user.id,
        )
    if project is not None and project_grants:
        role = Role(name=f"p-{new_id().hex[-8:]}", scope_kind="project")
        repo.add(role)
        await session.flush()
        for permission in project_grants:
            repo.grant(role.id, permission)
        repo.assign(
            role_id=role.id,
            scope=Scope.project(project.id),
            principal_kind="user",
            principal_id=user.id,
        )
    await session.flush()
    return Actor(
        user_id=user.id,
        email=user.email,
        is_active=True,
        mfa_verified=True,
        mfa_satisfied_at=utcnow(),
    )


async def _type(session: AsyncSession, name: str | None = None) -> AssetType:
    row = AssetType(name=name or f"종류-{new_id().hex[-6:]}")
    session.add(row)
    await session.flush()
    return row


async def _ticket(session: AsyncSession) -> tuple[Project, Issue]:
    project = Project(key=f"C{new_id().hex[-5:].upper()}", name="CMDB")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(state)
    kind = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(kind)
    await session.flush()
    issue = Issue(
        project_id=project.id,
        type_id=kind.id,
        state_id=state.id,
        key_seq=1,
        summary="프로젝터가 안 켜집니다",
    )
    session.add(issue)
    await session.flush()
    return project, issue


class TestRegistry:
    async def test_an_asset_carries_its_type_name(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """목록이 행마다 종류를 조회하면 N+1 이 된다."""
        kind = await _type(session, "프로젝터")
        actor = await _actor(session, global_grants=desk_perms.ALL)
        view = await AssetService(session, permissions).create(
            actor, NewAsset(type_id=kind.id, name="본관 3층 프로젝터")
        )
        assert view.type_name == "프로젝터"
        assert view.asset.status == "in_use"
        assert view.ticket_count == 0

    async def test_the_same_tag_twice_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        kind = await _type(session)
        actor = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        await service.create(actor, NewAsset(type_id=kind.id, name="하나", tag="A-1"))
        with pytest.raises(ConflictError) as caught:
            await service.create(actor, NewAsset(type_id=kind.id, name="둘", tag="a-1"))
        assert caught.value.code == "desk.asset_tag_taken"

    async def test_two_assets_without_a_tag_are_fine(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """교실과 서비스에는 자산번호가 없다. NULL 을 하나로 세면 두 번째를
        못 만든다."""
        kind = await _type(session)
        actor = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        await service.create(actor, NewAsset(type_id=kind.id, name="1반 교실"))
        await service.create(actor, NewAsset(type_id=kind.id, name="2반 교실"))
        page = await service.search(actor, PageRequest(limit=10))
        assert len(page.items) == 2

    async def test_an_archived_type_takes_no_new_assets(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """접은 것은 "이제 안 쓴다" 는 뜻이다. 그 뒤로 늘어나면 접은 의미가
        없다."""
        kind = await _type(session)
        actor = await _actor(session, global_grants=desk_perms.ALL)
        await AssetTypeService(session, permissions).set_archived(actor, kind.id, archived=True)
        with pytest.raises(ValidationError) as caught:
            await AssetService(session, permissions).create(
                actor, NewAsset(type_id=kind.id, name="늦은 것")
            )
        assert caught.value.code == "desk.asset_type_not_available"

    async def test_a_missing_owner_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        kind = await _type(session)
        actor = await _actor(session, global_grants=desk_perms.ALL)
        with pytest.raises(ValidationError) as caught:
            await AssetService(session, permissions).create(
                actor, NewAsset(type_id=kind.id, name="주인 없음", owner_id=uuid4())
            )
        assert caught.value.code == "desk.asset_owner_not_found"

    async def test_clearing_the_owner_needs_its_own_flag(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """부분 수정에서 `None` 은 "안 건드린다" 다. 한 필드로 둘을 표현하면
        폼이 값을 안 보내는 것만으로 담당자가 지워진다."""
        kind = await _type(session)
        owner = await _user(session)
        actor = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        made = await service.create(
            actor, NewAsset(type_id=kind.id, name="노트북", owner_id=owner.id)
        )
        untouched = await service.update(actor, made.asset.id, name="노트북 (수정)")
        assert untouched.asset.owner_id == owner.id
        cleared = await service.update(actor, made.asset.id, clear_owner=True)
        assert cleared.asset.owner_id is None

    async def test_a_stranger_cannot_register(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        kind = await _type(session)
        weak = await _actor(session, global_grants=[desk_perms.ASSET_VIEW])
        with pytest.raises(PermissionDeniedError):
            await AssetService(session, permissions).create(
                weak, NewAsset(type_id=kind.id, name="몰래")
            )

    async def test_someone_without_view_sees_nothing(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        nobody = await _actor(session)
        with pytest.raises(PermissionDeniedError):
            await AssetService(session, permissions).search(nobody, PageRequest(limit=10))


class TestSearch:
    async def _stock(
        self, session: AsyncSession, permissions: PermissionService, actor: Actor
    ) -> AssetType:
        kind = await _type(session)
        service = AssetService(session, permissions)
        await service.create(
            actor,
            NewAsset(type_id=kind.id, name="본관 프로젝터", tag="PJ-1", location="본관 3층"),
        )
        await service.create(
            actor, NewAsset(type_id=kind.id, name="별관 프로젝터", tag="PJ-2", location="별관")
        )
        await service.create(actor, NewAsset(type_id=kind.id, name="낡은 노트북", status="retired"))
        return kind

    async def test_it_finds_by_name_tag_and_location(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        actor = await _actor(session, global_grants=desk_perms.ALL)
        await self._stock(session, permissions, actor)
        service = AssetService(session, permissions)
        by_name = await service.search(actor, PageRequest(limit=10), query="별관")
        assert [view.asset.name for view in by_name.items] == ["별관 프로젝터"]
        # 자산번호는 대문자로 저장한다. 소문자로 쳐도 찾혀야 한다.
        by_tag = await service.search(actor, PageRequest(limit=10), query="pj-1")
        assert [view.asset.tag for view in by_tag.items] == ["PJ-1"]
        by_place = await service.search(actor, PageRequest(limit=10), query="3층")
        assert [view.asset.name for view in by_place.items] == ["본관 프로젝터"]

    async def test_retired_assets_stay_out_of_the_way(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓에 이을 자산을 고를 때 나간 장비가 후보에 섞이면 잘못 고른다."""
        actor = await _actor(session, global_grants=desk_perms.ALL)
        await self._stock(session, permissions, actor)
        service = AssetService(session, permissions)
        default = await service.search(actor, PageRequest(limit=10))
        assert "낡은 노트북" not in [view.asset.name for view in default.items]
        shown = await service.search(actor, PageRequest(limit=10), include_retired=True)
        assert "낡은 노트북" in [view.asset.name for view in shown.items]

    async def test_asking_for_retired_only_means_retired_only(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """ "나간 장비만 보여 달라" 도 정당한 요청이다. 기본 숨김이 그 말을
        지우면 그 화면을 만들 수 없다."""
        actor = await _actor(session, global_grants=desk_perms.ALL)
        await self._stock(session, permissions, actor)
        page = await AssetService(session, permissions).search(
            actor, PageRequest(limit=10), status="retired"
        )
        assert [view.asset.name for view in page.items] == ["낡은 노트북"]

    async def test_the_page_ends_with_a_cursor_not_a_wall(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**상한 없는 전체 목록을 주는 길이 없다.** 이 저장소는 목록을 통째로
        내리다 세 번 다쳤다 — 그때마다 마지막에 만든 것을 고를 수 없었다."""
        kind = await _type(session)
        actor = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        for index in range(5):
            await service.create(actor, NewAsset(type_id=kind.id, name=f"장비 {index:02d}"))
        first = await service.search(actor, PageRequest(limit=2), type_id=kind.id)
        assert len(first.items) == 2
        assert first.next_cursor is not None
        second = await service.search(
            actor, PageRequest(limit=2, cursor=first.next_cursor), type_id=kind.id
        )
        assert [view.asset.name for view in second.items] == ["장비 02", "장비 03"]

    async def test_it_can_narrow_to_one_customer_organization(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """ "A 학교 장비" 를 묻는 자리다 (C13)."""
        kind = await _type(session)
        org = CustomerOrganization(name=f"학교-{new_id().hex[-6:]}")
        session.add(org)
        await session.flush()
        actor = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        theirs = await service.create(
            actor, NewAsset(type_id=kind.id, name="그 학교 프로젝터", organization_id=org.id)
        )
        await service.create(actor, NewAsset(type_id=kind.id, name="우리 프로젝터"))
        page = await service.search(actor, PageRequest(limit=10), organization_id=org.id)
        assert [view.asset.id for view in page.items] == [theirs.asset.id]
        assert page.items[0].organization_name == org.name


class TestLinking:
    async def _ready(
        self, session: AsyncSession, permissions: PermissionService
    ) -> tuple[Project, Issue, Asset, Actor]:
        project, issue = await _ticket(session)
        kind = await _type(session)
        admin = await _actor(session, global_grants=desk_perms.ALL)
        made = await AssetService(session, permissions).create(
            admin, NewAsset(type_id=kind.id, name="본관 프로젝터", tag="PJ-9")
        )
        agent = await _actor(
            session,
            global_grants=[desk_perms.ASSET_VIEW],
            project=project,
            project_grants=["issue.view", "issue.edit"],
        )
        return project, issue, made.asset, agent

    async def test_linking_shows_up_on_the_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, issue, asset, agent = await self._ready(session, permissions)
        service = AssetService(session, permissions)
        await service.link(agent, issue.id, asset.id)
        found = await service.for_issue(agent, issue.id)
        assert [row.asset.id for row in found] == [asset.id]
        assert found[0].asset.tag == "PJ-9"

    async def test_the_same_asset_twice_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, issue, asset, agent = await self._ready(session, permissions)
        service = AssetService(session, permissions)
        await service.link(agent, issue.id, asset.id)
        with pytest.raises(ConflictError) as caught:
            await service.link(agent, issue.id, asset.id)
        assert caught.value.code == "desk.asset_already_linked"

    async def test_linking_needs_both_doors(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓을 고칠 수 있어야 하고, 자산을 볼 수 있어야 한다. 못 보는 것을
        이을 수는 없다."""
        project, issue, asset, _ = await self._ready(session, permissions)
        service = AssetService(session, permissions)

        no_asset = await _actor(
            session, project=project, project_grants=["issue.view", "issue.edit"]
        )
        with pytest.raises(PermissionDeniedError):
            await service.link(no_asset, issue.id, asset.id)

        no_ticket = await _actor(session, global_grants=[desk_perms.ASSET_VIEW])
        with pytest.raises(PermissionDeniedError):
            await service.link(no_ticket, issue.id, asset.id)

    async def test_unlinking_asks_only_about_the_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """자산을 못 보게 된 사람이 자기 티켓의 잘못된 링크를 못 떼면 그
        티켓은 틀린 채로 남는다."""
        project, issue, asset, agent = await self._ready(session, permissions)
        service = AssetService(session, permissions)
        await service.link(agent, issue.id, asset.id)

        editor = await _actor(session, project=project, project_grants=["issue.view", "issue.edit"])
        assert await service.unlink(editor, issue.id, asset.id) is True
        assert await service.for_issue(agent, issue.id) == []

    async def test_unlinking_something_that_was_not_linked_is_not_an_error(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, issue, asset, agent = await self._ready(session, permissions)
        assert await AssetService(session, permissions).unlink(agent, issue.id, asset.id) is False

    async def test_reading_the_links_needs_the_ticket_not_the_asset(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """여기서 답하는 질문은 "이 티켓이 무엇에 대한 것인가" 이고, 그건
        티켓의 내용이다."""
        project, issue, asset, agent = await self._ready(session, permissions)
        await AssetService(session, permissions).link(agent, issue.id, asset.id)
        stranger = await _actor(session, global_grants=desk_perms.ALL)
        with pytest.raises(PermissionDeniedError):
            await AssetService(session, permissions).for_issue(stranger, issue.id)
        del project

    async def test_the_asset_knows_its_tickets(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """자꾸 고장나는 장비를 찾는 것이 이 기능을 쓰는 이유 중 하나다."""
        _, issue, asset, agent = await self._ready(session, permissions)
        service = AssetService(session, permissions)
        await service.link(agent, issue.id, asset.id)
        admin = await _actor(session, global_grants=desk_perms.ALL)
        tickets = await service.tickets_of(admin, asset.id)
        assert [row.issue_id for row in tickets] == [issue.id]
        assert tickets[0].summary == "프로젝터가 안 켜집니다"
        (view,) = (await service.search(admin, PageRequest(limit=10), type_id=asset.type_id)).items
        assert view.ticket_count == 1


class TestDeleting:
    async def test_an_asset_with_tickets_cannot_be_deleted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이력을 지우는 길은 없다.** 장비가 나간 것이라면 `retired` 다."""
        project, issue = await _ticket(session)
        kind = await _type(session)
        admin = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        made = await service.create(admin, NewAsset(type_id=kind.id, name="프로젝터"))
        agent = await _actor(
            session,
            global_grants=[desk_perms.ASSET_VIEW],
            project=project,
            project_grants=["issue.view", "issue.edit"],
        )
        await service.link(agent, issue.id, made.asset.id)

        with pytest.raises(ConflictError) as caught:
            await service.delete(admin, made.asset.id)
        assert caught.value.code == "desk.asset_in_use"

        # 대신 사용 종료. 이력은 남고 후보에서는 빠진다.
        retired = await service.update(admin, made.asset.id, status="retired")
        assert retired.asset.status == "retired"
        assert len(await service.tickets_of(admin, made.asset.id)) == 1

    async def test_an_unlinked_asset_can_be_deleted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """잘못 만든 행을 치우는 길은 있어야 한다."""
        kind = await _type(session)
        admin = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetService(session, permissions)
        made = await service.create(admin, NewAsset(type_id=kind.id, name="오타"))
        await service.delete(admin, made.asset.id)
        with pytest.raises(NotFoundError):
            await service.get(admin, made.asset.id)

    async def test_a_missing_asset_is_not_found(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _actor(session, global_grants=desk_perms.ALL)
        with pytest.raises(NotFoundError):
            await AssetService(session, permissions).delete(admin, uuid4())


class TestTypes:
    async def test_types_come_back_in_the_order_the_admin_set(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetTypeService(session, permissions)
        await service.create(admin, name="프로젝터", position=2)
        await service.create(admin, name="노트북", position=1)
        rows = await service.list_all(admin)
        assert [row.name for row in rows] == ["노트북", "프로젝터"]

    async def test_the_same_name_twice_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        admin = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetTypeService(session, permissions)
        await service.create(admin, name="프로젝터")
        with pytest.raises(ConflictError) as caught:
            await service.create(admin, name="프로젝터")
        assert caught.value.code == "desk.asset_type_name_taken"

    async def test_an_archived_type_is_hidden_but_readable(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """지우지 않는 이유: 그 종류의 자산이 남아 있고, 지우면 그 자산들이
        가리킬 곳을 잃는다(FK 가 RESTRICT 다)."""
        admin = await _actor(session, global_grants=desk_perms.ALL)
        service = AssetTypeService(session, permissions)
        kind = await service.create(admin, name="낡은 종류")
        await service.set_archived(admin, kind.id, archived=True)
        assert kind.id not in [row.id for row in await service.list_all(admin)]
        assert kind.id in [row.id for row in await service.list_all(admin, include_archived=True)]

    async def test_a_stranger_cannot_add_a_type(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        weak = await _actor(session, global_grants=[desk_perms.ASSET_VIEW])
        with pytest.raises(PermissionDeniedError):
            await AssetTypeService(session, permissions).create(weak, name="몰래")


class TestOrganizationChoices:
    """자산에 붙일 고객 조직 후보 (C15).

    이 자리가 따로 있는 이유는 권한이다. 조직 관리 목록은 step-up 을 요구하고
    자산 등록은 일부러 아니다 — 관리 목록을 자산 화면에서 그대로 쓰면 화면을
    여는 것만으로 403 이 난다. 브라우저에서 실제로 그랬다.
    """

    async def test_asset_manage_is_enough(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 메서드가 있는 이유다.**

        `desk.customer.manage` 로 재면 자산을 등록하는 사람은 조직을 고를 수
        없다 — 그 손잡이는 step-up 을 요구하고, 자산 등록은 아니니까.
        """
        org = CustomerOrganization(name="가 학교")
        session.add(org)
        await session.flush()
        keeper = await _actor(session, global_grants=[desk_perms.ASSET_MANAGE])
        found = await AssetService(session, permissions).organizations(keeper)
        assert org.id in [row.id for row in found.items]

    async def test_reading_assets_alone_does_not_open_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """목록을 보는 것과 조직 전체를 훑는 것은 다르다. 후보 목록은 자산에
        붙지 않은 조직까지 준다."""
        weak = await _actor(session, global_grants=[desk_perms.ASSET_VIEW])
        with pytest.raises(PermissionDeniedError):
            await AssetService(session, permissions).organizations(weak)

    async def test_it_hands_back_the_name_and_nothing_else(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """넓히는 것은 일부러 하는 일이어야 한다. 관리 목록이 함께 주는
        소속 인원수·도메인·메모는 이 손잡이의 것이 아니다."""
        org = CustomerOrganization(name="나 학교", domains=["na.example.com"], note="비밀")
        session.add(org)
        await session.flush()
        keeper = await _actor(session, global_grants=[desk_perms.ASSET_MANAGE])
        found = await AssetService(session, permissions).organizations(keeper, query="나 학교")
        assert [row.name for row in found.items] == ["나 학교"]
        assert {field.name for field in fields(found.items[0])} == {"id", "name"}

    async def test_it_searches_by_name(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """드롭다운이 아니라 검색이다 — 고객 조직은 수백 개가 된다."""
        session.add_all(
            [CustomerOrganization(name="한빛 초등학교"), CustomerOrganization(name="두빛 중학교")]
        )
        await session.flush()
        keeper = await _actor(session, global_grants=[desk_perms.ASSET_MANAGE])
        found = await AssetService(session, permissions).organizations(keeper, query="한빛")
        assert [row.name for row in found.items] == ["한빛 초등학교"]

    async def test_an_archived_organization_is_not_a_candidate(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """계약이 끝난 조직에 새 장비를 붙이는 것은 실수다. 이미 붙어 있던
        자산은 그대로 남고 이름도 계속 보인다 — 후보에서만 뺀다."""
        gone = CustomerOrganization(name="떠난 학교", archived_at=utcnow())
        session.add(gone)
        await session.flush()
        keeper = await _actor(session, global_grants=[desk_perms.ASSET_MANAGE])
        found = await AssetService(session, permissions).organizations(keeper)
        assert gone.id not in [row.id for row in found.items]

    async def test_the_candidate_list_is_bounded(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """상한 없는 목록을 주는 자리를 만들지 않는다 (C15 파일 머리)."""
        session.add_all([CustomerOrganization(name=f"학교 {i:02d}") for i in range(12)])
        await session.flush()
        keeper = await _actor(session, global_grants=[desk_perms.ASSET_MANAGE])
        found = await AssetService(session, permissions).organizations(keeper, limit=5)
        assert len(found.items) == 5
        # **잘렸다고 말한다.** 조용히 자르면 "이게 전부" 로 읽힌다.
        assert found.has_more is True

    async def test_it_does_not_cry_cut_when_it_is_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """늘 "더 있다" 를 말하면 그 문구는 아무 뜻이 없어진다."""
        session.add(CustomerOrganization(name=f"딱 하나 {new_id().hex[-6:]}"))
        await session.flush()
        keeper = await _actor(session, global_grants=[desk_perms.ASSET_MANAGE])
        found = await AssetService(session, permissions).organizations(
            keeper, query="딱 하나", limit=5
        )
        assert len(found.items) == 1
        assert found.has_more is False
