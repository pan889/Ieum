"""큐와 정형 응답 (feature-map C3, C10).

붙잡는 것이 셋이다.

1. **큐가 권한을 넓히지 않는다.** 큐는 실행자의 `issue.view` ACL 로 돈다.
   만든 사람의 권한을 승계하면 큐를 공유하는 것이 곧 권한 상승이 된다.
2. **큐에는 티켓만 담긴다.** IQL 이 평범한 이슈까지 잡아도 걸러진다. 조건을
   `type = ...` 같은 이름으로 강제하지 않는 이유도 여기 있다 — 이름은
   관리자가 바꾼다.
3. **저장되고 실행이 실패하는 큐를 만들 수 없다.** 큐를 만든 사람은 자기
   큐를 눌러 보지 않으므로, 여기서 막지 않으면 사이드바에 이름만 있고 그
   큐로 들어와야 할 티켓들은 아무도 안 본다.
"""

from __future__ import annotations

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
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.service import (
    CannedResponseService,
    CustomerPortalService,
    PortalService,
    QueueService,
)
from ieum.modules.identity.models import User
from ieum.modules.issues import permissions as issue_perms
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState
from ieum.modules.issues.service import IssueService, NewIssue
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _person(session: AsyncSession, *, customer: bool = False) -> User:
    row = User(
        email=f"q-{new_id()}@example.com",
        display_name="Person",
        status="active",
        is_customer=customer,
    )
    session.add(row)
    await session.flush()
    return row


def _customer_actor(user: User) -> Actor:
    return Actor(user_id=user.id, email=user.email, is_customer=True, is_active=True)


async def _project(session: AsyncSession) -> Project:
    row = Project(key=f"Q{new_id().hex[-6:].upper()}", name="Desk")
    session.add(row)
    await session.flush()
    return row


async def _issue_type(session: AsyncSession, project: Project) -> IssueType:
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    session.add(
        WorkflowState(
            workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
        )
    )
    row = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(row)
    await session.flush()
    return row


async def _manager(session: AsyncSession, project: Project) -> User:
    """큐를 정의하고 티켓도 보는 사람. `queue.manage` + 이슈 권한."""
    person = await _person(session)
    await grant(
        session,
        principal_id=person.id,
        permissions_granted=(
            desk_perms.QUEUE_MANAGE,
            desk_perms.QUEUE_WORK,
            desk_perms.PORTAL_MANAGE,
            issue_perms.ISSUE_VIEW,
            issue_perms.ISSUE_CREATE,
        ),
        scope=Scope.project(project.id),
    )
    return person


async def _ticket_in(session: AsyncSession, permissions: PermissionService, project: Project):
    """이 프로젝트에 포털·요청 유형을 만들고 티켓 하나를 낸다."""
    issue_type = await _issue_type(session, project)
    manager = await _manager(session, project)
    slug = f"q{new_id().hex[-8:]}"
    service = PortalService(session, permissions)
    portal = (
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug=slug,
            description=None,
            theme={},
            is_public=False,
        )
    ).portal
    request_type = (
        await service.create_request_type(
            actor_for(manager),
            portal.id,
            issue_type_id=issue_type.id,
            name="Broken",
            description=None,
            icon=None,
            position=0,
            form_fields=[{"key": "summary", "label": "무엇이", "required": True}],
            field_mapping={},
            is_enabled=True,
        )
    ).request_type
    customer = await _person(session, customer=True)
    filed = await CustomerPortalService(session, permissions).submit(
        _customer_actor(customer),
        slug,
        request_type_id=request_type.id,
        answers={"summary": "프린터가 안 됩니다"},
    )
    return manager, issue_type, filed


class TestTheQueueHoldsOnlyTickets:
    async def test_a_plain_issue_in_the_same_project_is_not_in_the_queue(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 파일의 이유다.**

        큐의 조건은 프로젝트 하나뿐이라 평범한 이슈도 다 잡는다. 그런데
        큐에는 티켓만 나와야 한다 — 평범한 이슈에는 요청자도 창구도 없어서
        상담원 화면이 반쯤 빈 채로 그려지고, 그건 고객 요청이 아니다.
        """
        project = await _project(session)
        manager, issue_type, filed = await _ticket_in(session, permissions, project)
        plain = await IssueService(session, permissions).create(
            actor_for(manager),
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="평범한 이슈",
                description=None,
                custom_fields={},
            ),
        )

        queue = (
            await QueueService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="전체",
                iql=f"project = {project.key}",
                position=0,
            )
        ).queue
        page = await QueueService(session, permissions).run(
            actor_for(manager), queue.id, PageRequest(limit=50, cursor=None)
        )

        ids = {row.id for row in page.items}
        assert filed.issue.id in ids
        assert plain.issue.id not in ids, "평범한 이슈가 큐에 섞였다"

    async def test_the_queue_runs_with_the_callers_own_permissions(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """큐를 공유하는 것이 권한 상승이 되면 안 된다.

        큐를 만든 사람은 이 프로젝트를 보지만, 부르는 사람은 못 본다 —
        빈 목록이어야 한다. 만든 사람의 권한을 승계하면 여기서 티켓이 나온다.
        """
        project = await _project(session)
        manager, _, _ = await _ticket_in(session, permissions, project)
        queue = (
            await QueueService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="전체",
                iql=f"project = {project.key}",
                position=0,
            )
        ).queue

        # 큐 목록은 보지만 이슈는 못 보는 사람.
        outsider = await _person(session)
        await grant(
            session,
            principal_id=outsider.id,
            permissions_granted=(desk_perms.QUEUE_WORK,),
            scope=Scope.project(project.id),
        )
        page = await QueueService(session, permissions).run(
            actor_for(outsider), queue.id, PageRequest(limit=50, cursor=None)
        )
        assert page.items == []


class TestSavingAQueueValidatesIt:
    async def test_a_broken_query_is_refused_at_save_time(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """저장되고 실행이 실패하는 큐를 만들 수 없다.

        폼과 달리 여기서는 실패가 늦게 드러난다 — 큐를 만든 사람은 자기 큐를
        눌러 보지 않는다. 그 사이 그 큐로 들어와야 할 티켓은 아무도 안 본다.
        """
        project = await _project(session)
        manager = await _manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await QueueService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="틀린 큐",
                iql="assigne = 나",
                position=0,
            )
        # **IQL 쪽 코드가 그대로 나와야 한다.** 뭉뚱그린 검증 실패로 바꿔
        # 버리면 화면이 "질의가 틀렸다" 만 말하고 어디가 틀렸는지는 못 말한다.
        # 접두사는 `iql.` 다 — 한 번 `issues.` 로 적었고, 그렇게 두면 카탈로그에
        # 없는 코드가 화면에 그대로 찍힌다.
        assert exc.value.code == "iql.unknown_field", exc.value.code
        # 위치도 함께 온다. IQL 편집기가 그 자리를 표시한다.
        assert exc.value.details, "오류 위치가 빠졌다"

    async def test_an_empty_condition_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await QueueService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="빈 큐",
                iql="   ",
                position=0,
            )
        assert exc.value.code == "desk.queue_iql_empty"

    async def test_editing_to_a_broken_query_is_refused_too(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """만들 때만 보면, 고칠 때 망가진 큐가 들어온다."""
        project = await _project(session)
        manager = await _manager(session, project)
        queue = (
            await QueueService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="큐",
                iql=f"project = {project.key}",
                position=0,
            )
        ).queue
        with pytest.raises(ValidationError):
            await QueueService(session, permissions).update(
                actor_for(manager), queue.id, iql="assigne = 나"
            )

    async def test_a_duplicate_name_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """같은 이름의 큐가 둘이면 사이드바에서 구별할 수 없다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = QueueService(session, permissions)
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="열린 요청",
            iql=f"project = {project.key}",
            position=0,
        )
        with pytest.raises(ConflictError) as exc:
            await service.create(
                actor_for(manager),
                project_id=project.id,
                name="열린 요청",
                iql=f"project = {project.key}",
                position=1,
            )
        assert exc.value.code == "desk.queue_name_taken"

    async def test_archiving_frees_the_name(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """지운 큐의 이름은 **돌려받는다.**

        지우면 보관만 되는데(감사 로그가 이름을 들고 있다), 유일성 검사가
        보관된 것까지 세고 있었다. 그래서 큐를 지우면 목록에서는 사라지는데
        같은 이름을 다시 못 만들었고, 화면은 보이지도 않는 큐를 가리키며
        "같은 이름의 큐가 있다" 고 했다. 보관을 푸는 길도 없어서 그 이름은
        영영 죽었다.
        """
        project = await _project(session)
        manager = await _manager(session, project)
        service = QueueService(session, permissions)

        first = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="아직 안 맡은 것",
            iql=f"project = {project.key}",
            position=0,
        )
        await service.delete(actor_for(manager), first.queue.id)

        again = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="아직 안 맡은 것",
            iql=f"project = {project.key}",
            position=0,
        )
        assert again.queue.id != first.queue.id
        # 보관된 쪽은 그대로 남아 있다 — 이름을 풀어 준 것이지 지운 것이 아니다.
        assert (await service.list_for(actor_for(manager), project.id)) == [again.queue]

    async def test_a_live_name_is_still_refused_after_another_is_archived(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이름을 풀어 주는 것이 검사를 끄는 것이 되면 안 된다.

        보관된 것이 하나 있어도, **살아 있는 것끼리는** 여전히 겹칠 수 없다.
        """
        project = await _project(session)
        manager = await _manager(session, project)
        service = QueueService(session, permissions)

        buried = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="열린 요청",
            iql=f"project = {project.key}",
            position=0,
        )
        await service.delete(actor_for(manager), buried.queue.id)
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="열린 요청",
            iql=f"project = {project.key}",
            position=0,
        )
        with pytest.raises(ConflictError) as exc:
            await service.create(
                actor_for(manager),
                project_id=project.id,
                name="열린 요청",
                iql=f"project = {project.key}",
                position=1,
            )
        assert exc.value.code == "desk.queue_name_taken"

    async def test_renaming_onto_an_archived_name_works(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """새로 만드는 길만이 아니라 **고치는 길**도 막혀 있었다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = QueueService(session, permissions)

        buried = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="옛 이름",
            iql=f"project = {project.key}",
            position=0,
        )
        await service.delete(actor_for(manager), buried.queue.id)
        living = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="쓰는 이름",
            iql=f"project = {project.key}",
            position=0,
        )

        renamed = await service.update(actor_for(manager), living.queue.id, name="옛 이름")
        assert renamed.queue.name == "옛 이름"


class TestQueuePermissions:
    async def test_working_a_queue_does_not_let_you_define_one(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """큐의 조건은 상담원 전원이 무엇을 보는지 정한다. 일하는 권한과
        같은 손잡이에 두지 않는다."""
        project = await _project(session)
        agent = await _person(session)
        await grant(
            session,
            principal_id=agent.id,
            permissions_granted=(desk_perms.QUEUE_WORK, issue_perms.ISSUE_VIEW),
            scope=Scope.project(project.id),
        )
        with pytest.raises(PermissionDeniedError):
            await QueueService(session, permissions).create(
                actor_for(agent),
                project_id=project.id,
                name="내 큐",
                iql=f"project = {project.key}",
                position=0,
            )

    async def test_a_stranger_sees_no_queues(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        nobody = await _person(session)
        with pytest.raises(PermissionDeniedError):
            await QueueService(session, permissions).list_for(actor_for(nobody), project.id)

    async def test_a_deleted_queue_is_gone(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """보관이지만 밖에서는 없는 것과 같아야 한다 — 목록에도, 실행에도."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = QueueService(session, permissions)
        queue = (
            await service.create(
                actor_for(manager),
                project_id=project.id,
                name="큐",
                iql=f"project = {project.key}",
                position=0,
            )
        ).queue
        await service.delete(actor_for(manager), queue.id)

        assert await service.list_for(actor_for(manager), project.id) == []
        with pytest.raises(NotFoundError):
            await service.run(actor_for(manager), queue.id, PageRequest(limit=10, cursor=None))


class TestCannedResponses:
    async def test_archiving_frees_both_the_name_and_the_shortcut(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """큐와 같은 결함이 정형 응답에도 있었다 — 이름에 하나, 단축어에 하나.

        단축어 쪽이 더 아프다. `/환불` 은 상담원이 손가락으로 외운 것이라,
        문구를 다시 만들면서 같은 단축어를 못 쓰면 고친 것이 아니라 못 쓰게
        된 것이다.
        """
        project = await _project(session)
        manager = await _manager(session, project)
        service = CannedResponseService(session, permissions)

        old = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="환불 안내",
            body="영업일 3일 안에 처리됩니다.",
            shortcut="환불",
        )
        await service.delete(actor_for(manager), old.response.id)

        again = await service.create(
            actor_for(manager),
            project_id=project.id,
            name="환불 안내",
            body="영업일 2일로 줄었습니다.",
            shortcut="환불",
        )
        assert again.response.id != old.response.id
        assert again.response.shortcut == "환불"

    async def test_a_live_shortcut_is_still_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이름을 풀어 주는 것이 검사를 끄는 것이 되면 안 된다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = CannedResponseService(session, permissions)
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="환불 안내",
            body="내용",
            shortcut="환불",
        )
        with pytest.raises(ConflictError) as exc:
            await service.create(
                actor_for(manager),
                project_id=project.id,
                name="다른 이름",
                body="내용",
                shortcut="환불",
            )
        assert exc.value.code == "desk.canned_shortcut_taken"

    async def test_an_agent_reads_them_but_cannot_change_them(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        service = CannedResponseService(session, permissions)
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="환불 안내",
            body="영업일 3일 안에 처리됩니다.",
            shortcut="환불",
        )

        agent = await _person(session)
        await grant(
            session,
            principal_id=agent.id,
            permissions_granted=(desk_perms.QUEUE_WORK,),
            scope=Scope.project(project.id),
        )
        rows = await service.list_for(actor_for(agent), project.id)
        assert [r.name for r in rows] == ["환불 안내"]
        with pytest.raises(PermissionDeniedError):
            await service.create(
                actor_for(agent),
                project_id=project.id,
                name="내 문구",
                body="내용",
                shortcut=None,
            )

    async def test_a_shortcut_with_a_space_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`/` 로 부르는 자리라 공백이 들어가면 부를 수 없는 단축어가 된다."""
        project = await _project(session)
        manager = await _manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await CannedResponseService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="문구",
                body="내용",
                shortcut="환불 안내",
            )
        assert exc.value.code == "desk.canned_shortcut_invalid"

    async def test_the_leading_slash_is_stripped(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """사람은 `/환불` 이라고 적는다. 그대로 저장하면 `//환불` 로 불린다."""
        project = await _project(session)
        manager = await _manager(session, project)
        view = await CannedResponseService(session, permissions).create(
            actor_for(manager),
            project_id=project.id,
            name="문구",
            body="내용",
            shortcut="/환불",
        )
        assert view.response.shortcut == "환불"

    async def test_a_duplicate_shortcut_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """같은 단축어가 둘이면 어느 것이 나올지 사람이 알 수 없다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = CannedResponseService(session, permissions)
        await service.create(
            actor_for(manager), project_id=project.id, name="첫째", body="내용", shortcut="환불"
        )
        with pytest.raises(ConflictError) as exc:
            await service.create(
                actor_for(manager),
                project_id=project.id,
                name="둘째",
                body="내용",
                shortcut="환불",
            )
        assert exc.value.code == "desk.canned_shortcut_taken"

    async def test_two_responses_can_both_have_no_shortcut(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """단축어 없는 응답이 둘이면 유니크 제약에 걸리면 안 된다.
        Postgres 는 NULL 을 서로 다르게 보므로 통과하는데, 그게 의도라는
        것을 여기서 못 박는다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = CannedResponseService(session, permissions)
        await service.create(
            actor_for(manager), project_id=project.id, name="첫째", body="내용", shortcut=None
        )
        await service.create(
            actor_for(manager), project_id=project.id, name="둘째", body="내용", shortcut=""
        )
        rows = await service.list_for(actor_for(manager), project.id)
        assert sorted(r.name for r in rows) == ["둘째", "첫째"]
        assert all(r.shortcut is None for r in rows)

    async def test_editing_the_name_keeps_the_shortcut(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`shortcut: null` 을 "지워라" 로 읽으면 이름만 고치려는 요청이
        단축어를 함께 날린다. 지우는 손잡이는 따로다."""
        project = await _project(session)
        manager = await _manager(session, project)
        service = CannedResponseService(session, permissions)
        view = await service.create(
            actor_for(manager), project_id=project.id, name="문구", body="내용", shortcut="환불"
        )
        edited = await service.update(actor_for(manager), view.response.id, name="새 이름")
        assert edited.response.shortcut == "환불"

        cleared = await service.update(actor_for(manager), view.response.id, clear_shortcut=True)
        assert cleared.response.shortcut is None

    async def test_an_empty_body_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project = await _project(session)
        manager = await _manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await CannedResponseService(session, permissions).create(
                actor_for(manager), project_id=project.id, name="빈 문구", body="   ", shortcut=None
            )
        assert exc.value.code == "desk.canned_body_empty"
