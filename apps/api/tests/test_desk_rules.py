"""자동화 규칙이 실제로 도는 층 (feature-map C9).

`automation.py` 는 순수 함수라 값을 손으로 적어 시험했다. 여기는 **행을 쓰는
층**이다: 규칙이 걸리고, 조치가 티켓을 바꾸고, 고리가 안 생기는지 실제 DB 로
본다.

붙잡는 것 — 첫째가 이 파일의 이유다:

- **고리를 막는다.** 규칙이 코멘트를 남기면 그 코멘트가 다시
  `issue.commented` 를 내고, 같은 규칙이 또 걸린다. 자동화가 만든 이벤트에는
  표시가 붙고, 자동화는 그 표시가 붙은 이벤트를 안 본다.
- **사람이 잡은 티켓을 뺏지 않는다.** 규칙은 "아직 아무도 안 잡았으면" 을
  뜻한다.
- **티켓이 아닌 이슈에는 안 걸린다.** 걸면 이슈 전부가 데스크 규칙의 대상이
  되고, 그건 다른 제품이다.
- **저장할 때 다 본다.** 자동화의 실패는 조용하다 — 아무 일도 안 일어난 것과
  구별되지 않는다.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.events import EventEnvelope
from ieum.core.exceptions import ConflictError, PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk import rules as desk_rules
from ieum.modules.desk.models import (
    AutomationRule,
    CannedResponse,
    CustomerOrganization,
    Portal,
    RequestType,
    TicketExt,
)
from ieum.modules.desk.service import AutomationService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import (
    Issue,
    IssueComment,
    IssueType,
    Workflow,
    WorkflowState,
)
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant

SUBMITTED = "desk.ticket.submitted"


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _ticket(
    session: AsyncSession, *, priority: int = 3, channel: str = "portal"
) -> tuple[Project, Issue]:
    project = Project(key=f"R{new_id().hex[-6:].upper()}", name="Rules")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(state)
    issue_type = IssueType(project_id=project.id, name="Request", workflow_id=workflow.id)
    session.add(issue_type)
    customer = User(
        email=f"c-{new_id()}@example.com", display_name="고객", status="active", is_customer=True
    )
    session.add(customer)
    await session.flush()

    issue = Issue(
        project_id=project.id,
        type_id=issue_type.id,
        state_id=state.id,
        key_seq=1,
        summary="프린터가 안 됩니다",
        priority=priority,
    )
    session.add(issue)
    await session.flush()
    session.add(TicketExt(issue_id=issue.id, reporter_customer_id=customer.id, channel=channel))
    await session.flush()
    return project, issue


async def _request_type(session: AsyncSession, project: Project) -> tuple[Portal, RequestType]:
    """이 프로젝트의 창구와 요청 유형 하나. 조건이 지목할 대상이다."""
    portal = Portal(
        project_id=project.id, name=f"창구-{new_id().hex[-6:]}", slug=f"p-{new_id().hex[-8:]}"
    )
    session.add(portal)
    issue_type = (
        await session.execute(select(IssueType).where(IssueType.project_id == project.id).limit(1))
    ).scalar_one()
    await session.flush()
    row = RequestType(
        portal_id=portal.id, issue_type_id=issue_type.id, name=f"문의-{new_id().hex[-4:]}"
    )
    session.add(row)
    await session.flush()
    return portal, row


async def _rule(
    session: AsyncSession,
    project: Project,
    *,
    actions: list[dict[str, Any]],
    conditions: list[dict[str, Any]] | None = None,
    event: str = SUBMITTED,
    enabled: bool = True,
) -> AutomationRule:
    row = AutomationRule(
        project_id=project.id,
        name=f"rule-{new_id()}",
        trigger={"event": event},
        conditions=conditions or [],
        actions=actions,
        is_enabled=enabled,
    )
    session.add(row)
    await session.flush()
    return row


async def _agent(session: AsyncSession) -> User:
    row = User(email=f"a-{new_id()}@example.com", display_name="상담원", status="active")
    session.add(row)
    await session.flush()
    return row


def _envelope(issue: Issue, *, event: str = SUBMITTED, **payload: Any) -> EventEnvelope:
    return EventEnvelope(
        id=new_id(),
        event_type=event,
        aggregate_type="issue",
        aggregate_id=issue.id,
        payload=payload,
    )


async def _run(session: AsyncSession, envelope: EventEnvelope) -> int:
    return await desk_rules.handle_automation(desk_rules.RuleContext(session=session), envelope)


async def _comments(session: AsyncSession, issue: Issue) -> list[IssueComment]:
    return list(
        (await session.execute(select(IssueComment).where(IssueComment.issue_id == issue.id)))
        .scalars()
        .all()
    )


class TestRunningARule:
    async def test_it_raises_the_priority(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session, priority=2)
        await _rule(session, project, actions=[{"kind": "set_priority", "priority": 5}])

        assert await _run(session, _envelope(issue)) == 1
        await session.refresh(issue)
        assert issue.priority == 5

    async def test_it_assigns(self, session: AsyncSession, permissions: PermissionService) -> None:
        project, issue = await _ticket(session)
        agent = await _agent(session)
        await _rule(session, project, actions=[{"kind": "assign", "user_id": str(agent.id)}])

        await _run(session, _envelope(issue))
        await session.refresh(issue)
        assert issue.assignee_id == agent.id

    async def test_it_does_not_take_a_ticket_someone_picked_up(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """규칙은 "아직 아무도 안 잡았으면" 을 뜻한다. 뺏으면 두 사람이 같은
        일을 하거나 아무도 안 한다."""
        project, issue = await _ticket(session)
        mine = await _agent(session)
        theirs = await _agent(session)
        issue.assignee_id = mine.id
        await session.flush()
        await _rule(session, project, actions=[{"kind": "assign", "user_id": str(theirs.id)}])

        await _run(session, _envelope(issue))
        await session.refresh(issue)
        assert issue.assignee_id == mine.id

    async def test_it_replies_with_a_canned_response(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session)
        canned = CannedResponse(
            project_id=project.id, name="접수 안내", body="접수했습니다. 곧 연락드립니다."
        )
        session.add(canned)
        await session.flush()
        await _rule(
            session,
            project,
            actions=[{"kind": "reply_with_canned", "canned_response_id": str(canned.id)}],
        )

        await _run(session, _envelope(issue))
        comments = await _comments(session, issue)
        assert [c.body for c in comments] == ["접수했습니다. 곧 연락드립니다."]
        # 고객이 볼 수 있어야 자동 회신이다.
        assert comments[0].is_internal is False
        # 우리 사용자 중 누구도 아니다.
        assert comments[0].author_id is None

    async def test_a_note_stays_internal(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 구별이 무너지면 되돌릴 방법이 없다.**"""
        project, issue = await _ticket(session)
        canned = CannedResponse(project_id=project.id, name="메모", body="지난번에도 그랬음")
        session.add(canned)
        await session.flush()
        await _rule(
            session,
            project,
            actions=[{"kind": "add_note", "canned_response_id": str(canned.id)}],
        )

        await _run(session, _envelope(issue))
        assert (await _comments(session, issue))[0].is_internal is True

    async def test_actions_run_in_order(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session, priority=1)
        agent = await _agent(session)
        await _rule(
            session,
            project,
            actions=[
                {"kind": "set_priority", "priority": 4},
                {"kind": "assign", "user_id": str(agent.id)},
            ],
        )

        await _run(session, _envelope(issue))
        await session.refresh(issue)
        assert issue.priority == 4
        assert issue.assignee_id == agent.id

    async def test_a_missing_canned_response_does_not_stop_the_worker(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """규칙 하나가 워커를 죽이면 다른 티켓의 자동화도 멈춘다."""
        project, issue = await _ticket(session, priority=2)
        await _rule(
            session,
            project,
            actions=[
                {"kind": "reply_with_canned", "canned_response_id": str(new_id())},
                {"kind": "set_priority", "priority": 5},
            ],
        )

        assert await _run(session, _envelope(issue)) == 1
        await session.refresh(issue)
        # 뒤의 조치는 그대로 돌았다.
        assert issue.priority == 5


class TestNotRunning:
    async def test_an_automated_event_does_not_trigger_again(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 파일의 이유다.** 규칙이 남긴 코멘트가 같은 규칙을
        다시 부르면 무한 고리다."""
        project, issue = await _ticket(session)
        canned = CannedResponse(project_id=project.id, name="회신", body="확인했습니다")
        session.add(canned)
        await session.flush()
        await _rule(
            session,
            project,
            event="issue.commented",
            actions=[{"kind": "reply_with_canned", "canned_response_id": str(canned.id)}],
        )

        # 사람이 남긴 코멘트 → 규칙이 돈다.
        assert await _run(session, _envelope(issue, event="issue.commented")) == 1
        # 규칙이 남긴 코멘트 → 안 돈다.
        assert await _run(session, _envelope(issue, event="issue.commented", automated=True)) == 0
        assert len(await _comments(session, issue)) == 1

    async def test_the_reply_carries_the_mark(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """표시가 이벤트에 실려야 다음 드레인이 그것을 건너뛴다."""
        from ieum.core.outbox import OutboxEvent

        project, issue = await _ticket(session)
        canned = CannedResponse(project_id=project.id, name="회신", body="확인했습니다")
        session.add(canned)
        await session.flush()
        await _rule(
            session,
            project,
            actions=[{"kind": "reply_with_canned", "canned_response_id": str(canned.id)}],
        )
        await _run(session, _envelope(issue))

        rows = list(
            (
                await session.execute(
                    select(OutboxEvent).where(
                        OutboxEvent.aggregate_id == issue.id,
                        OutboxEvent.event_type == "issue.commented",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(rows) == 1
        assert rows[0].payload["automated"] is True

    async def test_a_disabled_rule_does_not_run(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session, priority=2)
        await _rule(
            session, project, actions=[{"kind": "set_priority", "priority": 5}], enabled=False
        )
        assert await _run(session, _envelope(issue)) == 0

    async def test_a_rule_for_another_event_does_not_run(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session, priority=2)
        await _rule(
            session,
            project,
            event="issue.transitioned",
            actions=[{"kind": "set_priority", "priority": 5}],
        )
        assert await _run(session, _envelope(issue)) == 0

    async def test_a_rule_from_another_project_does_not_run(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        _, issue = await _ticket(session, priority=2)
        other, _ = await _ticket(session)
        await _rule(session, other, actions=[{"kind": "set_priority", "priority": 5}])
        assert await _run(session, _envelope(issue)) == 0

    async def test_a_plain_issue_is_not_touched(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓이 아닌 이슈에 데스크 규칙을 걸면 그건 다른 제품이다."""
        project, issue = await _ticket(session)
        await _rule(session, project, actions=[{"kind": "set_priority", "priority": 5}])
        plain = Issue(
            project_id=project.id,
            type_id=issue.type_id,
            state_id=issue.state_id,
            key_seq=9100,
            summary="티켓이 아닌 이슈",
            priority=1,
        )
        session.add(plain)
        await session.flush()

        assert await _run(session, _envelope(plain)) == 0
        await session.refresh(plain)
        assert plain.priority == 1

    async def test_an_unmatched_condition_stops_the_rule(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session, priority=2)
        await _rule(
            session,
            project,
            conditions=[{"field": "priority", "op": "gte", "value": 4}],
            actions=[{"kind": "set_priority", "priority": 5}],
        )
        assert await _run(session, _envelope(issue)) == 0

    async def test_a_matched_condition_runs_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue = await _ticket(session, priority=5, channel="email")
        agent = await _agent(session)
        await _rule(
            session,
            project,
            conditions=[
                {"field": "priority", "op": "gte", "value": 4},
                {"field": "channel", "op": "eq", "value": "email"},
            ],
            actions=[{"kind": "assign", "user_id": str(agent.id)}],
        )
        assert await _run(session, _envelope(issue)) == 1


class TestSavingARule:
    async def _manager(self, session: AsyncSession, project: Project) -> User:
        row = User(email=f"m-{new_id()}@example.com", display_name="관리자", status="active")
        session.add(row)
        await session.flush()
        await grant(
            session,
            principal_id=row.id,
            permissions_granted=(desk_perms.AUTOMATION_MANAGE,),
            scope=Scope.project(project.id),
        )
        return row

    async def test_a_good_rule_saves(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        view = await AutomationService(session, permissions).create(
            actor_for(manager),
            project_id=project.id,
            name="긴급은 올린다",
            trigger={"event": SUBMITTED},
            conditions=[{"field": "priority", "op": "gte", "value": 4}],
            actions=[{"kind": "set_priority", "priority": 5}],
        )
        assert view.rule.name == "긴급은 올린다"

    async def test_a_rule_that_cannot_run_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await AutomationService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="망가진 규칙",
                trigger={"event": "issue.archived"},
                conditions=[],
                actions=[{"kind": "set_priority", "priority": 5}],
            )
        assert exc.value.code == "desk.automation_invalid"

    async def test_another_projects_canned_response_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """규칙은 저장되고 실행만 조용히 실패한다 — 그게 가장 나쁜 모양이다."""
        project, _ = await _ticket(session)
        other, _ = await _ticket(session)
        manager = await self._manager(session, project)
        canned = CannedResponse(project_id=other.id, name="남의 문구", body="안녕하세요")
        session.add(canned)
        await session.flush()

        with pytest.raises(ValidationError) as exc:
            await AutomationService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="남의 문구로 회신",
                trigger={"event": SUBMITTED},
                conditions=[],
                actions=[{"kind": "reply_with_canned", "canned_response_id": str(canned.id)}],
            )
        assert exc.value.code == "desk.automation_canned_unknown"

    async def test_assigning_to_a_customer_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객을 담당자로 두면 그 사람이 상담원 화면의 대상이 된다."""
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        customer = User(
            email=f"c-{new_id()}@example.com",
            display_name="고객",
            status="active",
            is_customer=True,
        )
        session.add(customer)
        await session.flush()

        with pytest.raises(ValidationError) as exc:
            await AutomationService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="고객에게 배정",
                trigger={"event": SUBMITTED},
                conditions=[],
                actions=[{"kind": "assign", "user_id": str(customer.id)}],
            )
        assert exc.value.code == "desk.automation_assignee_unknown"

    async def test_a_duplicate_name_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        service = AutomationService(session, permissions)
        body = {
            "project_id": project.id,
            "trigger": {"event": SUBMITTED},
            "conditions": [],
            "actions": [{"kind": "set_priority", "priority": 5}],
        }
        await service.create(actor_for(manager), name="같은 이름", **body)  # type: ignore[arg-type]
        with pytest.raises(ConflictError):
            await service.create(actor_for(manager), name="같은 이름", **body)  # type: ignore[arg-type]

    async def test_archiving_frees_the_name(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """규칙은 **반대 방향으로** 깨져 있었다.

        `_name_taken` 은 이미 살아 있는 것만 봤는데 DB 제약은 보관된 것까지
        봤다. 그래서 규칙을 지우고 같은 이름으로 다시 만들면 가드는 통과하고
        저장이 IntegrityError 로 500 이 났다 — 관리자는 무엇이 막았는지도 못
        들었다. 이제 둘이 같은 것을 본다.
        """
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        service = AutomationService(session, permissions)
        body = {
            "project_id": project.id,
            "trigger": {"event": SUBMITTED},
            "conditions": [],
            "actions": [{"kind": "set_priority", "priority": 5}],
        }
        first = await service.create(actor_for(manager), name="접수 알림", **body)  # type: ignore[arg-type]
        await service.delete(actor_for(manager), first.rule.id)

        again = await service.create(actor_for(manager), name="접수 알림", **body)  # type: ignore[arg-type]
        assert again.rule.id != first.rule.id

    async def test_another_projects_request_type_in_a_condition_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """조건이 남의 요청 유형을 가리키면 규칙은 저장되고 **한 번도 안 맞는다.**

        순수 층은 값이 UUID 인지까지만 본다 — DB 를 안 보기 때문이다. 여기서
        안 막으면 관리자는 "규칙이 안 도는" 화면만 보게 된다.
        """
        project, _ = await _ticket(session)
        other, _ = await _ticket(session)
        manager = await self._manager(session, project)
        _, request_type = await _request_type(session, other)

        with pytest.raises(ValidationError) as exc:
            await AutomationService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="남의 폼",
                trigger={"event": SUBMITTED},
                conditions=[
                    {"field": "request_type_id", "op": "eq", "value": str(request_type.id)}
                ],
                actions=[{"kind": "set_priority", "priority": 5}],
            )
        assert exc.value.code == "desk.automation_request_type_unknown"

    async def test_its_own_request_type_is_fine(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        _, request_type = await _request_type(session, project)

        view = await AutomationService(session, permissions).create(
            actor_for(manager),
            project_id=project.id,
            name="우리 폼",
            trigger={"event": SUBMITTED},
            conditions=[{"field": "request_type_id", "op": "eq", "value": str(request_type.id)}],
            actions=[{"kind": "set_priority", "priority": 5}],
        )
        assert view.rule.conditions[0]["value"] == str(request_type.id)

    async def test_an_unknown_organization_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        manager = await self._manager(session, project)
        with pytest.raises(ValidationError) as exc:
            await AutomationService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="없는 조직",
                trigger={"event": SUBMITTED},
                conditions=[{"field": "organization_id", "op": "eq", "value": str(new_id())}],
                actions=[{"kind": "set_priority", "priority": 5}],
            )
        assert exc.value.code == "desk.automation_organization_unknown"

    async def test_in_checks_every_item(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """하나만 남의 것이어도 그 갈래는 죽는다."""
        project, _ = await _ticket(session)
        other, _ = await _ticket(session)
        manager = await self._manager(session, project)
        _, mine = await _request_type(session, project)
        _, theirs = await _request_type(session, other)

        with pytest.raises(ValidationError) as exc:
            await AutomationService(session, permissions).create(
                actor_for(manager),
                project_id=project.id,
                name="둘 중 하나",
                trigger={"event": SUBMITTED},
                conditions=[
                    {
                        "field": "request_type_id",
                        "op": "in",
                        "value": [str(mine.id), str(theirs.id)],
                    }
                ],
                actions=[{"kind": "set_priority", "priority": 5}],
            )
        assert exc.value.code == "desk.automation_request_type_unknown"


class TestWhatCanBePicked:
    """**UUID 를 손으로 적게 하지 않는다.**

    요청 유형·고객 조직 목록은 각자의 권한이 지킨다. 규칙을 쓰는 사람에게 그
    둘을 마저 요구하면, 고를 수는 없는데 적으면 되는 자리가 된다.
    """

    async def test_it_lists_this_projects_forms_and_all_organizations(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        other, _ = await _ticket(session)
        manager = await TestSavingARule()._manager(session, project)
        portal, mine = await _request_type(session, project)
        _, theirs = await _request_type(session, other)
        org = CustomerOrganization(name=f"학교-{new_id().hex[-6:]}")
        session.add(org)
        await session.flush()

        found = await AutomationService(session, permissions).targets(
            actor_for(manager), project.id
        )
        ids = [row.id for row in found.request_types]
        assert mine.id in ids
        # 남의 프로젝트 폼은 안 보인다 — 걸어 봐야 안 맞는다.
        assert theirs.id not in ids
        # 포털 이름을 앞에 붙인다. 창구가 둘이면 "문의" 가 둘 보인다.
        assert any(row.name == f"{portal.name} / {mine.name}" for row in found.request_types)
        assert org.id in [row.id for row in found.organizations]

    async def test_a_stranger_gets_nothing(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        nobody = User(email=f"n-{new_id()}@example.com", display_name="아무나", status="active")
        session.add(nobody)
        await session.flush()
        with pytest.raises(PermissionDeniedError):
            await AutomationService(session, permissions).targets(actor_for(nobody), project.id)


class TestStrangers:
    async def test_a_stranger_cannot_list(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, _ = await _ticket(session)
        nobody = User(email=f"n-{new_id()}@example.com", display_name="아무나", status="active")
        session.add(nobody)
        await session.flush()
        with pytest.raises(PermissionDeniedError):
            await AutomationService(session, permissions).list_for(actor_for(nobody), project.id)
