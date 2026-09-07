"""내부 노트 vs 고객 회신 (feature-map C7).

**고객에게 내부 노트가 한 번이라도 새면 그걸로 끝이다.** 그래서 이 파일이
붙잡는 것은 하나다: 포털 표면으로는 내부 노트가 절대 나오지 않는다.

보장이 두 겹이라는 것도 함께 고정한다.

1. 이 티켓이 이 고객의 것인가 (아니면 404 — 있다는 사실도 알리지 않는다).
2. 공개 코멘트만 읽는 계약. **`include_internal` 매개변수가 없다** —
   매개변수로 두면 언젠가 어딘가에서 True 가 흘러 들어온다.

그리고 반대 방향: 상담원은 내부 노트를 **봐야** 한다. 조이다가 상담원까지
막으면 그건 고친 것이 아니다.
"""

from __future__ import annotations

import inspect

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import NotFoundError, PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.service import AgentTicketService, CustomerPortalService, PortalService
from ieum.modules.identity.models import User
from ieum.modules.issues import contracts as issues
from ieum.modules.issues import permissions as issue_perms
from ieum.modules.issues.models import IssueType, Workflow, WorkflowState
from ieum.modules.issues.service import CommentService, IssueService, NewIssue
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
        email=f"p-{new_id()}@example.com",
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
    row = Project(key=f"R{new_id().hex[-6:].upper()}", name="Desk")
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


SUMMARY_FIELD = {"key": "summary", "label": "무엇이 필요하신가요", "required": True}


async def _portal_and_type(
    session: AsyncSession, permissions: PermissionService, *, slug: str, is_public: bool
) -> tuple[Project, User, object]:
    project = await _project(session)
    manager = await _person(session)
    await grant(
        session,
        principal_id=manager.id,
        permissions_granted=(desk_perms.PORTAL_MANAGE,),
        scope=Scope.project(project.id),
    )
    issue_type = await _issue_type(session, project)
    service = PortalService(session, permissions)
    portal = (
        await service.create(
            actor_for(manager),
            project_id=project.id,
            name="Help",
            slug=slug,
            description=None,
            theme={},
            is_public=is_public,
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
            form_fields=[SUMMARY_FIELD],
            field_mapping={},
            is_enabled=True,
        )
    ).request_type
    return project, manager, request_type


async def _agent(session: AsyncSession, project: Project, *, internal: bool = True) -> User:
    person = await _person(session)
    granted = [
        desk_perms.QUEUE_WORK,
        issue_perms.ISSUE_VIEW,
        issue_perms.COMMENT_ADD,
    ]
    if internal:
        granted.append(issue_perms.COMMENT_VIEW_INTERNAL)
    await grant(
        session,
        principal_id=person.id,
        permissions_granted=tuple(granted),
        scope=Scope.project(project.id),
    )
    return person


async def _ticket(
    session: AsyncSession, permissions: PermissionService, *, slug: str
) -> tuple[Project, User, User, object]:
    """포털·요청 유형·고객·티켓 하나. 상담원도 함께 만든다."""
    project, _, request_type = await _portal_and_type(
        session, permissions, slug=slug, is_public=False
    )
    customer = await _person(session, customer=True)
    view = await CustomerPortalService(session, permissions).submit(
        _customer_actor(customer),
        slug,
        request_type_id=request_type.id,  # type: ignore[attr-defined]
        answers={"summary": "프린터가 안 됩니다"},
    )
    return project, customer, await _agent(session, project), view


class TestTheInternalNoteNeverLeaks:
    async def test_a_customer_does_not_see_an_internal_note(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이 파일이 있는 이유. 한 번 새면 그걸로 끝이다."""
        _, customer, agent, view = await _ticket(session, permissions, slug="leak")
        issue_id = view.issue.id  # type: ignore[attr-defined]
        comments = CommentService(session, permissions)
        await comments.add(actor_for(agent), issue_id, "고객에게 보이는 회신")
        await comments.add(actor_for(agent), issue_id, "내부 노트: 3층 배선 문제", is_internal=True)

        replies = await CustomerPortalService(session, permissions).list_replies(
            _customer_actor(customer), "leak", issue_id
        )
        bodies = [r.body for r in replies]
        assert bodies == ["고객에게 보이는 회신"]
        assert not any("내부 노트" in body for body in bodies)

    def test_the_public_contract_has_no_switch(self) -> None:
        """**`include_internal` 매개변수가 없다.**

        있으면 언젠가 어딘가에서 True 가 흘러 들어온다. 없으면 그럴 수 없다.
        서명 자체를 시험한다 — 주석으로 적어 두는 것과 다르다.
        """
        signature = inspect.signature(issues.list_public_comments)
        assert set(signature.parameters) == {"session", "issue_id"}, (
            "공개 코멘트 계약에 매개변수가 늘었다. `include_internal` 이 아닌지 확인하라."
        )

    async def test_an_agent_still_sees_the_internal_note(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """조이다가 상담원까지 막으면 그건 고친 것이 아니다."""
        _, _, agent, view = await _ticket(session, permissions, slug="agent")
        issue_id = view.issue.id  # type: ignore[attr-defined]
        comments = CommentService(session, permissions)
        await comments.add(actor_for(agent), issue_id, "공개 회신")
        await comments.add(actor_for(agent), issue_id, "내부 노트", is_internal=True)

        seen = await comments.list_for(actor_for(agent), issue_id)
        assert [c.body for c in seen] == ["공개 회신", "내부 노트"]

    async def test_an_agent_without_the_permission_sees_only_public(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """권한 없는 상담원에게도 내부 노트는 안 보인다 — SQL 단계에서 빠진다."""
        project, _, agent, view = await _ticket(session, permissions, slug="junior")
        issue_id = view.issue.id  # type: ignore[attr-defined]
        comments = CommentService(session, permissions)
        await comments.add(actor_for(agent), issue_id, "공개 회신")
        await comments.add(actor_for(agent), issue_id, "내부 노트", is_internal=True)

        junior = await _agent(session, project, internal=False)
        seen = await comments.list_for(actor_for(junior), issue_id)
        assert [c.body for c in seen] == ["공개 회신"]

    async def test_someone_without_the_permission_cannot_write_an_internal_note(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """내부 노트를 볼 수 없는 사람이 쓸 수는 더더욱 없다."""
        project, _, _, view = await _ticket(session, permissions, slug="nonote")
        junior = await _agent(session, project, internal=False)
        with pytest.raises(PermissionDeniedError):
            await CommentService(session, permissions).add(
                actor_for(junior),
                view.issue.id,  # type: ignore[attr-defined]
                "몰래",
                is_internal=True,
            )


class TestTheCustomerReply:
    async def test_a_customer_replies_and_it_is_public(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객에게는 `issue.comment.add` 가 없다. 그래도 자기 요청에는 쓴다."""
        _, customer, agent, view = await _ticket(session, permissions, slug="reply")
        issue_id = view.issue.id  # type: ignore[attr-defined]
        service = CustomerPortalService(session, permissions)
        written = await service.reply(
            _customer_actor(customer), "reply", issue_id, "아직 안 됩니다"
        )
        assert written.body == "아직 안 됩니다"

        # 상담원 쪽에서도 보인다 — 안 보이면 티켓이 멈춘다.
        seen = await CommentService(session, permissions).list_for(actor_for(agent), issue_id)
        assert [c.body for c in seen] == ["아직 안 됩니다"]
        assert seen[0].is_internal is False
        assert seen[0].author_id == customer.id

    async def test_an_empty_reply_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, customer, _, view = await _ticket(session, permissions, slug="empty")
        with pytest.raises(ValidationError) as exc:
            await CustomerPortalService(session, permissions).reply(
                _customer_actor(customer),
                "empty",
                view.issue.id,  # type: ignore[attr-defined]
                "   ",
            )
        assert exc.value.code == "issues.comment_empty"

    async def test_a_customer_cannot_reply_to_someone_elses_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """404 다. 남의 티켓이 **있다는 사실**도 알려 주지 않는다."""
        _, _, _, view = await _ticket(session, permissions, slug="notmine")
        stranger = await _person(session, customer=True)
        with pytest.raises(NotFoundError):
            await CustomerPortalService(session, permissions).reply(
                _customer_actor(stranger),
                "notmine",
                view.issue.id,  # type: ignore[attr-defined]
                "끼어들기",
            )

    async def test_a_customer_cannot_read_someone_elses_conversation(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, _, agent, view = await _ticket(session, permissions, slug="notyours")
        issue_id = view.issue.id  # type: ignore[attr-defined]
        await CommentService(session, permissions).add(actor_for(agent), issue_id, "공개 회신")
        stranger = await _person(session, customer=True)
        with pytest.raises(NotFoundError):
            await CustomerPortalService(session, permissions).list_replies(
                _customer_actor(stranger), "notyours", issue_id
            )


class TestTheAgentView:
    async def test_it_says_who_asked_and_from_where(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, customer, agent, view = await _ticket(session, permissions, slug="whoasked")
        seen = await AgentTicketService(session, permissions).get(
            actor_for(agent),
            view.issue.id,  # type: ignore[attr-defined]
        )
        assert seen is not None
        assert seen.request_type_name == "Broken"
        assert seen.portal_slug == "whoasked"
        assert seen.ticket.channel == "portal"
        assert seen.requester is not None
        assert seen.requester.user_id == customer.id
        # 계정 주소는 초대 메일을 받아 비밀번호를 정한 주소다.
        assert seen.requester.verified is True

    async def test_a_guest_address_is_marked_unverified(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """게스트가 적은 주소는 검증되지 않았다. 화면이 그걸 알아야 한다 —
        아니면 그 주소를 계정 주소처럼 믿고 확인 없이 무엇이든 보낸다."""
        project, _, request_type = await _portal_and_type(
            session, permissions, slug="guestmark", is_public=True
        )
        filed = await CustomerPortalService(session, permissions).submit_as_guest(
            "guestmark",
            request_type_id=request_type.id,  # type: ignore[attr-defined]
            email="parent@school.example.com",
            name="학부모",
            answers={"summary": "프린터"},
        )
        agent = await _agent(session, project)
        seen = await AgentTicketService(session, permissions).get(actor_for(agent), filed.issue.id)
        assert seen is not None
        assert seen.requester is not None
        assert seen.requester.email == "parent@school.example.com"
        assert seen.requester.verified is False

    async def test_a_real_issue_that_is_not_a_ticket_answers_none(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**`None` 이다. 오류가 아니다.**

        한동안 404 였고, 그래서 상담원이 평범한 이슈를 열 때마다 콘솔에
        404 가 찍혔다 — 이슈 상세는 가장 많이 열리는 화면이다. E2E 의
        `consoleErrors` 픽스처가 이슈 스펙 넷을 붉게 만들어 드러났다.

        **없는 id 로 시험하지 않는다.** 처음에는 `new_id()` 를 넘겼고, 그건
        "모르는 id" 만 본다 — 화면이 실제로 겪는 것은 그게 아니다. 그리고
        그 시험은 무력했다: 티켓 여부를 보는 가드를 지워도 통과한다(없는
        이슈는 그 다음 줄에서 걸리기 때문이다). 되돌려 확인하면서 알았다.
        """
        project, _, agent, _ = await _ticket(session, permissions, slug="plain")
        issue_type = await _issue_type(session, project)
        author = await _person(session)
        await grant(
            session,
            principal_id=author.id,
            permissions_granted=(issue_perms.ISSUE_CREATE, issue_perms.ISSUE_VIEW),
            scope=Scope.project(project.id),
        )
        plain = await IssueService(session, permissions).create(
            actor_for(author),
            NewIssue(
                project_id=project.id,
                type_id=issue_type.id,
                summary="평범한 이슈",
                description=None,
                custom_fields={},
            ),
        )

        assert (
            await AgentTicketService(session, permissions).get(actor_for(agent), plain.issue.id)
            is None
        )

    async def test_an_unknown_id_also_answers_none(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """모르는 id 도 `None` 이다. **묻는 순서가 그렇게 만든다.**

        "티켓인가" 를 먼저 보므로 모르는 id 는 거기서 이미 아니다. 이슈가
        없다는 사실을 따로 알릴 필요도 없다: `ticket_ext.issue_id` 는
        `issue.id` 를 가리키는 PK 겸 FK 라(`ON DELETE CASCADE`) 티켓이
        있으면 이슈도 있다. 그래서 이 서비스에서 "이슈가 없다" 는
        데이터베이스가 허용하지 않는 상태이고, 화면은 애초에 실재하는
        이슈의 id 로만 묻는다.
        """
        _, _, agent, _ = await _ticket(session, permissions, slug="unknown")
        assert (
            await AgentTicketService(session, permissions).get(actor_for(agent), new_id()) is None
        )

    async def test_a_stranger_gets_no_desk_facts(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """큐 권한이 없으면 `None` 이다.

        403 이 아닌 이유는 위와 같다(콘솔). 그리고 이슈는 볼 수 있는 사람에게
        "이건 티켓인데 너는 볼 수 없다" 를 알려 줄 이유가 없다 — 답은 어느
        쪽이든 "데스크 정보를 그리지 않는다" 로 같다.
        """
        _, _, _, view = await _ticket(session, permissions, slug="stranger")
        nobody = await _person(session)
        assert (
            await AgentTicketService(session, permissions).get(
                actor_for(nobody),
                view.issue.id,  # type: ignore[attr-defined]
            )
            is None
        )
