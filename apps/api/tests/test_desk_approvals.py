"""승인 단계 (feature-map C12).

승인은 **틀리면 조용히 위험한** 기능이다. 두 방향 다 비싸고, 둘 다 화면에
안 나타난다:

- 통과시켜서는 안 될 것을 통과시키면, 승인을 요구한 요청(권한 부여, 지출)이
  승인 없이 처리된다.
- 통과시켜야 할 것을 막으면, 요청이 영원히 멈춰 있고 사람은 제품이 고장났다고
  여긴다.

그래서 판정은 값으로 붙잡고(위쪽), 문이 실제로 잠기는지는 실제 DB 로 본다
(아래쪽). 붙잡는 것:

- **거절은 한 사람으로 끝난다.** `all` 모드에서 나머지를 기다리면 이미
  답이 정해진 요청이 멈춰 있다.
- **명단이 비면 통과가 아니다.** `all` 모드에서 "전원 동의" 가 참이 되어
  버리는 자리다.
- **요청자는 자기 요청을 승인하지 못한다.** 그리고 명단을 찍을 때 빠지므로
  "승인자가 셋인데 아무도 승인할 수 없는" 상태가 곧바로 보인다.
- **한 사람은 한 번만 결정한다.** DB 가 막는다.
- **기다리는 동안 착수도 종료도 못 한다.** 하나만 막으면 남은 길로 돌아간다.
- **문을 여는 길은 승인·거절·취소뿐이다.** 관리자라고 통과하지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import (
    ConflictError,
    PermissionDeniedError,
    ValidationError,
)
from ieum.core.ids import new_id
from ieum.core.outbox import OutboxEvent
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.core.time import utcnow
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.approvals import (
    PENDING_CODE,
    ApprovalService,
    Rule,
    Vote,
    blocks,
    gate,
    outcome,
    parse_rule,
    request_for,
    snapshot,
    validate_rule,
)
from ieum.modules.desk.models import Approval, ApprovalVote, Portal, RequestType
from ieum.modules.identity import contracts as identity
from ieum.modules.identity.models import GroupMember, User, UserGroup
from ieum.modules.issues import gates
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.issues.service import IssueService, SecurityLevelGuard
from ieum.modules.org.models import Project, Role
from ieum.modules.org.repository import OrgPermissionResolver, RoleRepository

# ── 값으로 붙잡는 것 ────────────────────────────────────────────


class TestRule:
    def test_a_rule_survives_a_round_trip(self) -> None:
        raw = {"mode": "all", "user_ids": [str(uuid4())], "group_ids": [str(uuid4())]}
        rule = validate_rule(raw)
        assert parse_rule(rule.to_json()) == rule

    def test_a_rule_without_approvers_is_refused(self) -> None:
        """승인자 없는 규칙은 **요청을 영원히 멈추는 스위치**다."""
        with pytest.raises(ValidationError) as caught:
            validate_rule({"mode": "one", "user_ids": [], "group_ids": []})
        assert caught.value.code == "desk.approvers_required"

    def test_an_unknown_mode_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_rule({"mode": "majority", "user_ids": [str(uuid4())]})
        assert caught.value.code == "desk.invalid_approval_mode"

    def test_too_many_approvers_is_refused(self) -> None:
        with pytest.raises(ValidationError) as caught:
            validate_rule({"mode": "one", "user_ids": [str(uuid4()) for _ in range(21)]})
        assert caught.value.code == "desk.too_many_approvers"

    def test_reading_a_broken_rule_gives_nothing_instead_of_raising(self) -> None:
        """**포털을 500 으로 만들지 않는다.** 설정이 망가진 요청 유형 하나가
        폼 목록 전체를 못 열게 하면 안 된다 — 저장 시점에 막는 것이 우리 몫이고,
        읽는 자리는 조용히 "승인 없음" 으로 둔다."""
        assert parse_rule(None) is None
        assert parse_rule("nope") is None
        assert parse_rule({"mode": "majority"}) is None

    def test_unreadable_ids_are_dropped_not_fatal(self) -> None:
        rule = parse_rule({"mode": "one", "user_ids": ["not-a-uuid", str(uuid4())]})
        assert rule is not None
        assert len(rule.user_ids) == 1


class TestSnapshot:
    def _rule(self, users: Sequence[UUID] = (), groups: Sequence[UUID] = ()) -> Rule:
        return Rule(mode="one", user_ids=tuple(users), group_ids=tuple(groups))

    def test_groups_and_names_are_merged_and_sorted(self) -> None:
        one, two = uuid4(), uuid4()
        found = snapshot(
            rule=self._rule(users=[one]),
            group_members=[two, one],
            active_user_ids=[one, two],
            reporter_id=None,
        )
        assert found == sorted([one, two])

    def test_the_reporter_is_left_out(self) -> None:
        """자기 승인이 허용되는 순간 이 기능은 서류 작업이 된다."""
        me, other = uuid4(), uuid4()
        found = snapshot(
            rule=self._rule(users=[me, other]),
            group_members=[],
            active_user_ids=[me, other],
            reporter_id=me,
        )
        assert found == [other]

    def test_the_reporter_being_the_only_approver_shows_as_empty(self) -> None:
        """**결정할 때 거절하지 않고 명단에서 뺀다.** 그러면 "승인자가 없다" 가
        곧바로 화면에 보이고, 상담원이 그 자리에서 취소할 수 있다."""
        me = uuid4()
        assert (
            snapshot(
                rule=self._rule(users=[me]),
                group_members=[],
                active_user_ids=[me],
                reporter_id=me,
            )
            == []
        )

    def test_people_who_cannot_approve_are_dropped(self) -> None:
        """정지된 계정·고객 계정이 명단에 남으면 `all` 모드가 절대 완성되지
        않는다. 그건 기다리는 것이 아니라 멈춘 것이다."""
        live, gone = uuid4(), uuid4()
        found = snapshot(
            rule=self._rule(users=[live, gone]),
            group_members=[],
            active_user_ids=[live],
            reporter_id=None,
        )
        assert found == [live]


class TestOutcome:
    def test_one_approval_is_enough_in_one_mode(self) -> None:
        one, two = uuid4(), uuid4()
        result = outcome(
            mode="one",
            approver_ids=[one, two],
            votes=[Vote(user_id=one, decision="approve")],
        )
        assert result == "approved"

    def test_all_mode_waits_for_everyone(self) -> None:
        one, two = uuid4(), uuid4()
        assert (
            outcome(
                mode="all",
                approver_ids=[one, two],
                votes=[Vote(user_id=one, decision="approve")],
            )
            == "pending"
        )
        assert (
            outcome(
                mode="all",
                approver_ids=[one, two],
                votes=[
                    Vote(user_id=one, decision="approve"),
                    Vote(user_id=two, decision="approve"),
                ],
            )
            == "approved"
        )

    def test_one_decline_ends_it_even_in_all_mode(self) -> None:
        """나머지에게 물어봐야 답이 달라지지 않고, 물어보는 동안 요청이
        멈춰 있다."""
        one, two, three = uuid4(), uuid4(), uuid4()
        assert (
            outcome(
                mode="all",
                approver_ids=[one, two, three],
                votes=[
                    Vote(user_id=one, decision="approve"),
                    Vote(user_id=two, decision="decline"),
                ],
            )
            == "declined"
        )

    def test_no_votes_is_pending(self) -> None:
        assert outcome(mode="one", approver_ids=[uuid4()], votes=[]) == "pending"

    def test_an_empty_list_never_counts_as_everyone(self) -> None:
        """**이 파일에서 가장 조용한 자리다.** `set() >= set()` 은 참이므로,
        명단이 빈 `all` 승인은 아무 표 없이 "전원 동의" 가 되어 버린다.
        그건 승인이 아니라 통과다 — 명단이 빈 승인은 사람이 취소해야 한다."""
        assert outcome(mode="all", approver_ids=[], votes=[]) == "pending"
        assert (
            outcome(
                mode="all",
                approver_ids=[],
                votes=[Vote(user_id=uuid4(), decision="approve")],
            )
            == "pending"
        )


class TestBlocks:
    def test_starting_and_finishing_are_both_blocked(self) -> None:
        """하나만 막으면 남은 길로 돌아간다 — 착수만 막으면 상담원이 바로
        "해결" 로 보낼 수 있고, 그러면 승인은 없던 것이 된다."""
        assert blocks(to_category="in_progress")
        assert blocks(to_category="done")

    def test_moving_inside_todo_stays_open(self) -> None:
        """분류는 요청을 처리하는 일이 아니다."""
        assert not blocks(to_category="todo")


# ── 실제 DB ─────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    service.register_guard(Issue, SecurityLevelGuard())
    set_permission_service(service)
    return service


@pytest.fixture(autouse=True)
def _installed_gate() -> object:
    """관문을 꽂는다. 앱에서는 `wiring.py` 가 하고, 여기서는 시험이 한다 —
    등록은 전역이므로 시험이 끝나면 지운다."""
    gates.clear()
    gates.register(gate)
    yield None
    gates.clear()


class Fixture:
    """한 프로젝트 + 워크플로우 + 요청 유형 + 티켓 하나."""

    def __init__(self) -> None:
        self.project: Project
        self.request_type: RequestType
        self.issue: Issue
        self.todo: WorkflowState
        self.doing: WorkflowState
        self.done: WorkflowState
        self.triage: WorkflowState


async def _user(session: AsyncSession, *, customer: bool = False, active: bool = True) -> User:
    row = User(
        email=f"u-{new_id()}@example.com",
        display_name=f"사람 {new_id().hex[-6:]}",
        status="active" if active else "suspended",
        is_customer=customer,
    )
    session.add(row)
    await session.flush()
    return row


async def _group(session: AsyncSession, members: Sequence[User]) -> UserGroup:
    group = UserGroup(name=f"g-{new_id().hex[-8:]}", source="local")
    session.add(group)
    await session.flush()
    for member in members:
        session.add(GroupMember(group_id=group.id, user_id=member.id))
    await session.flush()
    return group


async def _setup(session: AsyncSession, *, approval: dict[str, object] | None) -> Fixture:
    out = Fixture()
    out.project = Project(key=f"A{new_id().hex[-5:].upper()}", name="Approvals")
    session.add(out.project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    out.todo = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    out.triage = WorkflowState(
        workflow_id=workflow.id, name="Triage", category="todo", position=1, is_initial=False
    )
    out.doing = WorkflowState(
        workflow_id=workflow.id, name="Doing", category="in_progress", position=2, is_initial=False
    )
    out.done = WorkflowState(
        workflow_id=workflow.id, name="Done", category="done", position=3, is_initial=False
    )
    session.add_all([out.todo, out.triage, out.doing, out.done])
    issue_type = IssueType(project_id=out.project.id, name="Request", workflow_id=workflow.id)
    session.add(issue_type)
    await session.flush()

    # 슬러그는 **뒤쪽**을 쓴다. UUIDv7 의 앞은 시각이라, 한 시험 안에서 두
    # 포털을 만들면 같은 밀리초에 같은 접두사가 나온다 — 실제로 그랬다.
    portal = Portal(project_id=out.project.id, name="Help", slug=f"p-{new_id().hex[-10:]}")
    session.add(portal)
    await session.flush()
    out.request_type = RequestType(
        portal_id=portal.id,
        issue_type_id=issue_type.id,
        name="접근 요청",
        approval=approval,
    )
    session.add(out.request_type)
    await session.flush()

    out.issue = Issue(
        project_id=out.project.id,
        type_id=issue_type.id,
        state_id=out.todo.id,
        key_seq=1,
        summary="계정을 열어 주세요",
    )
    session.add(out.issue)
    await session.flush()
    return out


async def _agent(session: AsyncSession, project: Project, grants: Sequence[str]) -> Actor:
    user = await _user(session)
    repo = RoleRepository(session)
    role = Role(name=f"r-{new_id().hex[-8:]}", scope_kind="project")
    repo.add(role)
    await session.flush()
    for permission in grants:
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


def _actor_of(user: User) -> Actor:
    return Actor(
        user_id=user.id,
        email=user.email,
        is_active=True,
        mfa_verified=True,
        mfa_satisfied_at=utcnow(),
    )


async def _transition(
    session: AsyncSession,
    permissions: PermissionService,
    fixture: Fixture,
    actor: Actor,
    to_state: WorkflowState,
) -> None:
    """전이 하나를 만들어 실행한다. 관문이 걸리는지 보는 것이 목적이다."""
    transition = await _transition_row(session, fixture, to_state)
    await IssueService(session, permissions).transition(actor, fixture.issue.id, transition)


async def _transition_row(session: AsyncSession, fixture: Fixture, to_state: WorkflowState) -> UUID:
    from ieum.modules.issues.models import WorkflowTransition

    row = WorkflowTransition(
        workflow_id=to_state.workflow_id,
        name=f"to-{to_state.name}",
        from_state_id=None,
        to_state_id=to_state.id,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _pending(session: AsyncSession, issue_id: UUID) -> Approval | None:
    return (
        await session.execute(
            select(Approval).where(Approval.issue_id == issue_id, Approval.status == "pending")
        )
    ).scalar_one_or_none()


class TestRequesting:
    async def test_a_type_without_a_rule_asks_for_nothing(self, session: AsyncSession) -> None:
        """대부분의 요청 유형이 그렇다. 승인 행을 만들면 그 티켓은 막힌다."""
        fixture = await _setup(session, approval=None)
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is None
        assert await _pending(session, fixture.issue.id) is None

    async def test_the_group_is_expanded_at_request_time(self, session: AsyncSession) -> None:
        one, two = await _user(session), await _user(session)
        group = await _group(session, [one, two])
        fixture = await _setup(
            session, approval={"mode": "all", "user_ids": [], "group_ids": [str(group.id)]}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        assert set(made.approver_ids) == {str(one.id), str(two.id)}

    async def test_the_group_contract_itself_skips_suspended_people(
        self, session: AsyncSession
    ) -> None:
        """**계약을 따로 붙잡는다.** `request_for` 도 한 번 더 거르므로
        (고객 계정을 빼려고 사람을 읽는다), 아래 시험만으로는 이쪽 필터를
        지워도 초록이다 — 되돌려 보고 알았다. 안 걸린 필터는 조용히 사라진다.
        """
        live = await _user(session)
        gone = await _user(session, active=False)
        group = await _group(session, [live, gone])
        found = await identity.active_group_members(session, [group.id])
        assert found == frozenset({live.id})

    async def test_a_suspended_member_is_not_in_the_snapshot(self, session: AsyncSession) -> None:
        live = await _user(session)
        gone = await _user(session, active=False)
        group = await _group(session, [live, gone])
        fixture = await _setup(
            session, approval={"mode": "all", "user_ids": [], "group_ids": [str(group.id)]}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        assert made.approver_ids == [str(live.id)]

    async def test_a_customer_named_directly_is_not_in_the_snapshot(
        self, session: AsyncSession
    ) -> None:
        """고객은 포털 밖을 볼 수 없어서 승인 화면에 닿지 못한다. 설정 화면도
        막지만, 그 뒤에 계정이 고객으로 바뀔 수 있다."""
        staff = await _user(session)
        customer = await _user(session, customer=True)
        fixture = await _setup(
            session,
            approval={
                "mode": "one",
                "user_ids": [str(staff.id), str(customer.id)],
                "group_ids": [],
            },
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        assert made.approver_ids == [str(staff.id)]

    async def test_an_empty_snapshot_still_creates_a_pending_approval(
        self, session: AsyncSession
    ) -> None:
        """**안 만들면 승인 없이 처리되고 아무 화면에도 그 사실이 안 나온다.**
        만들면 "승인자가 없다" 로 보이고 상담원이 취소할 수 있다."""
        group = await _group(session, [])
        fixture = await _setup(
            session, approval={"mode": "all", "user_ids": [], "group_ids": [str(group.id)]}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        assert made.approver_ids == []
        assert made.status == "pending"


class TestNotifying:
    """알림이 이 기능을 살린다 — 승인자는 티켓을 보고 있지 않다."""

    async def test_the_request_names_the_approvers_and_the_ticket(
        self, session: AsyncSession
    ) -> None:
        """**`to_user_ids` 가 없으면 결정할 사람에게 알림이 안 간다.** 승인자는
        워처도 담당자도 아니라서 `notify` 의 기본 수신자에 안 들어온다.

        그리고 제목이 없으면 알림이 "ENG-12 의 승인이 필요합니다: " 로 나간다 —
        무엇을 승인하는지 없는 알림이다.
        """
        one, two = await _user(session), await _user(session)
        fixture = await _setup(
            session,
            approval={"mode": "all", "user_ids": [str(one.id), str(two.id)], "group_ids": []},
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == fixture.issue.id,
                    OutboxEvent.event_type == "desk.approval.requested",
                )
            )
        ).scalar_one()
        assert set(row.payload["to_user_ids"]) == {str(one.id), str(two.id)}
        assert row.payload["summary"] == "계정을 열어 주세요"
        assert row.payload["issue_key"].endswith("-1")

    async def test_the_decision_says_which_way_it_went(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        await ApprovalService(session, permissions).decide(
            _actor_of(approver), made.id, decision="decline"
        )
        row = (
            await session.execute(
                select(OutboxEvent).where(
                    OutboxEvent.aggregate_id == fixture.issue.id,
                    OutboxEvent.event_type == "desk.approval.decided",
                )
            )
        ).scalar_one()
        # 결과가 없으면 알림 제목을 고를 수 없다 — 승인과 거절은 받는 사람이
        # 해야 할 다음 일이 다르다.
        assert row.payload["status"] == "declined"
        assert row.payload["summary"] == "계정을 열어 주세요"

    async def test_a_type_without_a_rule_publishes_nothing(self, session: AsyncSession) -> None:
        fixture = await _setup(session, approval=None)
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        found = await session.scalar(
            select(func.count())
            .select_from(OutboxEvent)
            .where(OutboxEvent.event_type == "desk.approval.requested")
        )
        assert found == 0


class TestTheGate:
    async def test_starting_work_is_refused_while_pending(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        agent = await _agent(session, fixture.project, ["issue.view", "issue.transition"])
        with pytest.raises(ConflictError) as caught:
            await _transition(session, permissions, fixture, agent, fixture.doing)
        assert caught.value.code == PENDING_CODE

    async def test_finishing_is_refused_too(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """착수만 막으면 상담원이 바로 "해결" 로 보낼 수 있다."""
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        agent = await _agent(session, fixture.project, ["issue.view", "issue.transition"])
        with pytest.raises(ConflictError) as caught:
            await _transition(session, permissions, fixture, agent, fixture.done)
        assert caught.value.code == PENDING_CODE

    async def test_sorting_inside_todo_still_works(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        agent = await _agent(session, fixture.project, ["issue.view", "issue.transition"])
        await _transition(session, permissions, fixture, agent, fixture.triage)
        assert fixture.issue.state_id == fixture.triage.id

    async def test_an_approved_ticket_moves(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        await ApprovalService(session, permissions).decide(
            _actor_of(approver), made.id, decision="approve"
        )
        agent = await _agent(session, fixture.project, ["issue.view", "issue.transition"])
        await _transition(session, permissions, fixture, agent, fixture.doing)
        assert fixture.issue.state_id == fixture.doing.id

    async def test_a_declined_ticket_can_be_closed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """거절된 요청은 닫아야 한다. 거절이 문을 계속 잠그면 그 티켓은
        영원히 `todo` 에 남는다."""
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        await ApprovalService(session, permissions).decide(
            _actor_of(approver), made.id, decision="decline", comment="권한 범위 밖"
        )
        agent = await _agent(session, fixture.project, ["issue.view", "issue.transition"])
        await _transition(session, permissions, fixture, agent, fixture.done)
        assert fixture.issue.state_id == fixture.done.id

    async def test_cancelling_opens_the_door(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """명단이 빈 승인을 푸는 유일한 길이다."""
        group = await _group(session, [])
        fixture = await _setup(
            session, approval={"mode": "all", "user_ids": [], "group_ids": [str(group.id)]}
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        agent = await _agent(
            session,
            fixture.project,
            ["issue.view", "issue.transition", desk_perms.APPROVAL_MANAGE],
        )
        await ApprovalService(session, permissions).cancel(agent, made.id)
        await _transition(session, permissions, fixture, agent, fixture.doing)
        assert fixture.issue.state_id == fixture.doing.id

    async def test_a_ticket_without_an_approval_is_untouched(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """관문은 승인이 있는 티켓만 본다. 이슈 대부분은 티켓조차 아니다."""
        fixture = await _setup(session, approval=None)
        agent = await _agent(session, fixture.project, ["issue.view", "issue.transition"])
        await _transition(session, permissions, fixture, agent, fixture.doing)
        assert fixture.issue.state_id == fixture.doing.id


class TestDeciding:
    async def _pending_for(
        self, session: AsyncSession, approvers: Sequence[User], mode: str = "one"
    ) -> tuple[Fixture, Approval]:
        fixture = await _setup(
            session,
            approval={
                "mode": mode,
                "user_ids": [str(person.id) for person in approvers],
                "group_ids": [],
            },
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        return fixture, made

    async def test_a_stranger_cannot_decide(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """근거는 권한이 아니라 **명단**이다. 관리자도 남의 승인을 대신 주지
        못한다 — 대신 줄 수 있으면 명단이 뜻을 잃는다."""
        approver = await _user(session)
        stranger = await _user(session)
        _, made = await self._pending_for(session, [approver])
        with pytest.raises(PermissionDeniedError) as caught:
            await ApprovalService(session, permissions).decide(
                _actor_of(stranger), made.id, decision="approve"
            )
        assert caught.value.code == "desk.not_an_approver"

    async def test_one_person_decides_once(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`all` 모드에서 한 사람이 두 번 승인해 셈을 채우지 못한다."""
        one, two = await _user(session), await _user(session)
        _, made = await self._pending_for(session, [one, two], mode="all")
        service = ApprovalService(session, permissions)
        await service.decide(_actor_of(one), made.id, decision="approve")
        with pytest.raises(ConflictError) as caught:
            await service.decide(_actor_of(one), made.id, decision="approve")
        assert caught.value.code == "desk.already_decided"
        assert made.status == "pending"

    async def test_all_mode_finishes_when_everyone_agrees(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        one, two = await _user(session), await _user(session)
        _, made = await self._pending_for(session, [one, two], mode="all")
        service = ApprovalService(session, permissions)
        after_first = await service.decide(_actor_of(one), made.id, decision="approve")
        assert after_first.status == "pending"
        after_second = await service.decide(_actor_of(two), made.id, decision="approve")
        assert after_second.status == "approved"
        assert made.decided_at is not None

    async def test_a_decline_ends_it_immediately(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        one, two = await _user(session), await _user(session)
        _, made = await self._pending_for(session, [one, two], mode="all")
        view = await ApprovalService(session, permissions).decide(
            _actor_of(one), made.id, decision="decline", comment="예산 없음"
        )
        assert view.status == "declined"
        assert view.votes[0].comment == "예산 없음"

    async def test_a_finished_approval_takes_no_more_votes(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        one, two = await _user(session), await _user(session)
        _, made = await self._pending_for(session, [one, two], mode="one")
        service = ApprovalService(session, permissions)
        await service.decide(_actor_of(one), made.id, decision="approve")
        with pytest.raises(ConflictError) as caught:
            await service.decide(_actor_of(two), made.id, decision="decline")
        assert caught.value.code == "desk.approval_closed"

    async def test_an_unknown_decision_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        _, made = await self._pending_for(session, [approver])
        with pytest.raises(ValidationError) as caught:
            await ApprovalService(session, permissions).decide(
                _actor_of(approver), made.id, decision="maybe"
            )
        assert caught.value.code == "desk.invalid_decision"

    async def test_the_vote_is_recorded_with_its_author(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        _, made = await self._pending_for(session, [approver])
        await ApprovalService(session, permissions).decide(
            _actor_of(approver), made.id, decision="approve"
        )
        rows = list(
            (await session.execute(select(ApprovalVote).where(ApprovalVote.approval_id == made.id)))
            .scalars()
            .all()
        )
        assert [(row.user_id, row.decision) for row in rows] == [(approver.id, "approve")]

    async def test_cancelling_needs_the_permission(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture, made = await self._pending_for(session, [approver])
        weak = await _agent(session, fixture.project, ["issue.view"])
        with pytest.raises(PermissionDeniedError):
            await ApprovalService(session, permissions).cancel(weak, made.id)

    async def test_cancelling_a_finished_approval_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture, made = await self._pending_for(session, [approver])
        await ApprovalService(session, permissions).decide(
            _actor_of(approver), made.id, decision="approve"
        )
        agent = await _agent(session, fixture.project, [desk_perms.APPROVAL_MANAGE])
        with pytest.raises(ConflictError) as caught:
            await ApprovalService(session, permissions).cancel(agent, made.id)
        assert caught.value.code == "desk.approval_closed"

    async def test_the_cancelling_person_is_recorded(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture, made = await self._pending_for(session, [approver])
        agent = await _agent(session, fixture.project, [desk_perms.APPROVAL_MANAGE])
        await ApprovalService(session, permissions).cancel(agent, made.id)
        assert made.cancelled_by == agent.user_id


class TestReading:
    async def test_my_list_shows_only_what_i_can_still_decide(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이 목록이 이 기능의 안전장치다. 승인자가 요청을 못 보면 요청은
        영원히 멈춰 있고 아무도 그것을 모른다."""
        me, other = await _user(session), await _user(session)
        fixture = await _setup(
            session,
            approval={"mode": "all", "user_ids": [str(me.id), str(other.id)], "group_ids": []},
        )
        made = await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert made is not None
        service = ApprovalService(session, permissions)
        assert [view.id for view in await service.mine(_actor_of(me))] == [made.id]

        await service.decide(_actor_of(me), made.id, decision="approve")
        # 냈으면 내 목록에서 빠진다. 남은 것은 상대의 몫이다.
        assert await service.mine(_actor_of(me)) == []
        assert [view.id for view in await service.mine(_actor_of(other))] == [made.id]

    async def test_someone_else_does_not_see_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver, stranger = await _user(session), await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        assert await ApprovalService(session, permissions).mine(_actor_of(stranger)) == []

    async def test_the_history_needs_permission_to_see_the_ticket(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """거절 이유는 티켓 내용만큼 민감하다."""
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        elsewhere = await _setup(session, approval=None)
        stranger = await _agent(session, elsewhere.project, ["issue.view"])
        with pytest.raises(PermissionDeniedError):
            await ApprovalService(session, permissions).for_issue(stranger, fixture.issue.id)

    async def test_the_history_carries_the_names(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        approver = await _user(session)
        fixture = await _setup(
            session, approval={"mode": "one", "user_ids": [str(approver.id)], "group_ids": []}
        )
        await request_for(
            session,
            issue_id=fixture.issue.id,
            request_type=fixture.request_type,
            reporter_id=None,
        )
        agent = await _agent(session, fixture.project, ["issue.view"])
        (view,) = await ApprovalService(session, permissions).for_issue(agent, fixture.issue.id)
        assert [person.display_name for person in view.approvers] == [approver.display_name]
        assert view.can_decide is False
