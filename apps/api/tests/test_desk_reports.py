"""데스크 리포트 — SLA 성적과 상담원별 성적 (feature-map C14).

`sla_clock` 이 이미 답을 갖고 있으므로 여기서 붙잡는 것은 **세는 규칙**이다.
틀리게 세는 리포트는 조용히 틀린다 — 숫자가 나오고, 아무도 그 숫자가 다른
숫자여야 했다는 것을 모른다.

붙잡는 것:

- **위반은 `target_at` 과 지금을 비교해서 정한다.** `breached_at` 은 "알렸다"
  는 뜻이고, 스윕이 아직 안 훑은 위반은 그 값이 비어 있다 — 그걸로 세면
  위반이 **적게** 나오고, 적게 나오는 방향이 하필 듣기 좋은 방향이다.
- **안 끝났는데 목표를 지난 시계도 위반이다.** `completed_at IS NOT NULL` 만
  세면 지금 터지고 있는 것이 안 보이는데, 그게 리포트가 가장 도움 될 자리다.
- **네 갈래의 합이 총계와 맞는다.** 맞지 않으면 어느 칸이 거짓말인지 말할 수
  없다.
- **평균 해결 시간은 끝난 것만으로 정의된다.** 하나도 안 끝났으면 `0` 이
  아니라 `None` 이다 — `0` 은 "즉시 해결" 로 읽힌다.
- **담당자 없는 티켓도 한 칸이다.** 빼면 담당자별 합이 총계와 어긋나고,
  하필 가장 봐야 할 무리가 사라진다.
- **정책·달력을 고쳐도 지난 판정이 안 바뀐다.** `target_at` 을 저장한 이유가
  그것이고, 리포트가 되계산하면 그 보장이 깨진다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.context import Actor
from ieum.core.exceptions import PermissionDeniedError, ValidationError
from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, Scope, set_permission_service
from ieum.modules.desk import permissions as desk_perms
from ieum.modules.desk.models import BusinessCalendarRow, SlaClock, SlaPolicy, TicketExt
from ieum.modules.desk.reports import MAX_DAYS, AgentRow, DeskReport, DeskReportService
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver
from role_grants import actor_for, grant

HOUR = 3600

#: 창. 리포트는 **이 안에 만들어진** 티켓을 센다.
WINDOW_FROM = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
WINDOW_TO = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)
#: 리포트를 보는 "지금". 고정해 둔다 — `utcnow()` 로 두면 목표를 지난 시계와
#: 아직 도는 시계를 시험이 시계에 맡기게 된다.
NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)
INSIDE = datetime(2026, 9, 10, 9, 0, tzinfo=UTC)

ALWAYS_OPEN = {str(day): [["00:00", "23:59"]] for day in range(7)}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


class Desk:
    """한 프로젝트의 데스크 한 벌. 시험이 쓰는 손잡이만 낸다."""

    def __init__(
        self, project: Project, state: WorkflowState, issue_type: IssueType, customer: User
    ) -> None:
        self.project = project
        self.state = state
        self.issue_type = issue_type
        #: 티켓에는 **요청한 사람이 있어야 한다** — `ticket_ext` 의 체크
        #: 제약이 그렇게 걸려 있다. 요청자 없는 티켓은 회신할 곳이 없다.
        self.customer = customer
        self._seq = 0

    async def ticket(
        self,
        session: AsyncSession,
        *,
        created_at: datetime = INSIDE,
        assignee: User | None = None,
        resolved_at: datetime | None = None,
        archived: bool = False,
        is_ticket: bool = True,
    ) -> Issue:
        """티켓 하나. `is_ticket=False` 면 **데스크가 아닌 그냥 이슈**다.

        그냥 이슈를 만들 수 있어야 "티켓만 센다" 를 볼 수 있다 — 없으면 그
        조건이 있으나 없으나 시험이 똑같이 초록이다.
        """
        self._seq += 1
        row = Issue(
            project_id=self.project.id,
            type_id=self.issue_type.id,
            state_id=self.state.id,
            key_seq=self._seq,
            summary=f"티켓 {self._seq}",
            assignee_id=None if assignee is None else assignee.id,
        )
        session.add(row)
        await session.flush()
        # `created_at` 은 서버 기본값이라 만든 뒤에 덮어쓴다. 창을 시험하려면
        # 창 밖에 있는 행이 있어야 하고, 그건 "지금" 으로는 만들 수 없다.
        row.created_at = created_at
        row.resolved_at = resolved_at
        if archived:
            row.archived_at = created_at + timedelta(days=1)
        await session.flush()
        if is_ticket:
            session.add(
                TicketExt(issue_id=row.id, reporter_customer_id=self.customer.id, channel="portal")
            )
            await session.flush()
        return row

    async def policy(
        self, session: AsyncSession, *, name: str = "응답", seconds: int = 4 * HOUR
    ) -> SlaPolicy:
        calendar = BusinessCalendarRow(
            name=f"cal-{new_id()}", timezone="Asia/Seoul", working_hours=ALWAYS_OPEN, holidays=[]
        )
        session.add(calendar)
        await session.flush()
        row = SlaPolicy(
            project_id=self.project.id,
            name=f"{name}-{new_id().hex[-4:]}",
            metric="first_response",
            calendar_id=calendar.id,
            goals=[{"seconds": seconds}],
            pause_state_ids=[],
            escalations=[],
        )
        session.add(row)
        await session.flush()
        return row

    async def clock(
        self,
        session: AsyncSession,
        issue: Issue,
        policy: SlaPolicy,
        *,
        target_at: datetime,
        completed_at: datetime | None = None,
        breached_at: datetime | None = None,
    ) -> SlaClock:
        """시계를 **손으로** 심는다.

        `desk_clock.start_clocks` 를 거치지 않는 이유: 이 파일이 보는 것은
        세는 규칙이고, 시계가 제대로 걸리는지는 `test_desk_clock.py` 가 본다.
        손으로 심으면 "안 끝났는데 목표를 지났고 아직 안 알린" 조합을 정확히
        만들 수 있다 — 그게 스윕이 늦은 순간의 실제 모양이다.
        """
        row = SlaClock(
            issue_id=issue.id,
            policy_id=policy.id,
            started_at=issue.created_at,
            target_at=target_at,
            completed_at=completed_at,
            breached_at=breached_at,
            goal_seconds=4 * HOUR,
        )
        session.add(row)
        await session.flush()
        return row


async def _desk(session: AsyncSession) -> Desk:
    project = Project(key=f"D{new_id().hex[-6:].upper()}", name="데스크 리포트")
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
    return Desk(project, state, issue_type, customer)


async def _agent(session: AsyncSession, desk: Desk, *, name: str = "상담원") -> tuple[User, Actor]:
    row = User(email=f"ag-{new_id()}@example.com", display_name=name, status="active")
    session.add(row)
    await session.flush()
    await grant(
        session,
        principal_id=row.id,
        permissions_granted=(desk_perms.QUEUE_WORK,),
        scope=Scope.project(desk.project.id),
    )
    return row, actor_for(row)


async def _report(
    session: AsyncSession, permissions: PermissionService, desk: Desk, actor: Actor
) -> DeskReport:
    return await DeskReportService(session, permissions).report(
        actor,
        project_id=desk.project.id,
        starts_at=WINDOW_FROM,
        ends_at=WINDOW_TO,
        now=NOW,
    )


class TestCountingSla:
    async def test_an_unfinished_clock_past_its_target_is_overdue(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**아직 안 알린 위반도 위반이다.**

        스윕은 15초마다 돌고, 그 사이에 목표를 지난 시계는 `breached_at` 이
        비어 있다. 그 값으로 세면 위반이 **적게** 나온다 — 그리고 적게 나오는
        방향이 하필 듣기 좋은 방향이라, 틀렸다는 신호가 안 온다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        issue = await desk.ticket(session)
        await desk.clock(
            session,
            issue,
            policy,
            target_at=NOW - timedelta(hours=1),
            completed_at=None,
            breached_at=None,  # 스윕이 아직 안 훑었다
        )

        report = await _report(session, permissions, desk, actor)

        assert len(report.sla) == 1
        outcome = report.sla[0]
        assert outcome.overdue == 1, "안 끝났는데 목표를 지난 시계가 안 잡혔다"
        assert outcome.breached == 1
        assert outcome.running == 0
        assert outcome.met == 0 and outcome.missed == 0

    async def test_a_clock_finished_late_is_missed(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        issue = await desk.ticket(session)
        target = INSIDE + timedelta(hours=4)
        await desk.clock(
            session, issue, policy, target_at=target, completed_at=target + timedelta(minutes=1)
        )

        outcome = (await _report(session, permissions, desk, actor)).sla[0]
        assert outcome.missed == 1
        assert outcome.met == 0 and outcome.overdue == 0
        assert outcome.breached == 1

    async def test_a_clock_finished_in_time_is_met(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        issue = await desk.ticket(session)
        target = INSIDE + timedelta(hours=4)
        await desk.clock(
            session, issue, policy, target_at=target, completed_at=target - timedelta(minutes=1)
        )

        outcome = (await _report(session, permissions, desk, actor)).sla[0]
        assert outcome.met == 1
        assert outcome.breached == 0

    async def test_a_clock_finished_exactly_on_target_is_met(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """정확히 목표 시각에 끝난 것은 **지킨 것이다.**

        `>=` 로 잘못 적으면 딱 맞춘 티켓이 위반이 된다. 경계는 한쪽으로만
        갈 수 있고, 약속을 지킨 쪽이다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        issue = await desk.ticket(session)
        target = INSIDE + timedelta(hours=4)
        await desk.clock(session, issue, policy, target_at=target, completed_at=target)

        outcome = (await _report(session, permissions, desk, actor)).sla[0]
        assert outcome.met == 1
        assert outcome.missed == 0

    async def test_a_clock_still_inside_its_target_is_running(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        issue = await desk.ticket(session)
        await desk.clock(session, issue, policy, target_at=NOW + timedelta(hours=1))

        outcome = (await _report(session, permissions, desk, actor)).sla[0]
        assert outcome.running == 1
        assert outcome.breached == 0
        assert outcome.overdue == 0

    async def test_the_four_buckets_add_up_to_the_total(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**칸의 합이 총계와 맞는다.**

        네 갈래는 서로 겹치지도, 빈틈을 남기지도 않아야 한다. 겹치면 합이
        크고, 빈틈이 있으면 작다 — 둘 다 어느 칸이 거짓말인지 말할 수 없다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        target = INSIDE + timedelta(hours=4)
        plans: list[tuple[datetime, datetime | None]] = [
            (target, target - timedelta(minutes=5)),  # met
            (target, target + timedelta(minutes=5)),  # missed
            (NOW - timedelta(hours=2), None),  # overdue
            (NOW + timedelta(hours=2), None),  # running
            (NOW + timedelta(hours=3), None),  # running
        ]
        for target_at, completed_at in plans:
            issue = await desk.ticket(session)
            await desk.clock(session, issue, policy, target_at=target_at, completed_at=completed_at)

        outcome = (await _report(session, permissions, desk, actor)).sla[0]
        assert outcome.total == len(plans)
        assert (outcome.met, outcome.missed, outcome.overdue, outcome.running) == (1, 1, 1, 2)
        assert outcome.breached == outcome.missed + outcome.overdue

    async def test_each_policy_gets_its_own_row(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """정책을 합치면 안 된다 — 응답을 지키고 해결을 놓친 것과 그 반대가
        같은 숫자가 된다."""
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        response = await desk.policy(session, name="응답")
        resolution = await desk.policy(session, name="해결")
        issue = await desk.ticket(session)
        target = INSIDE + timedelta(hours=4)
        await desk.clock(session, issue, response, target_at=target, completed_at=target)
        await desk.clock(session, issue, resolution, target_at=NOW - timedelta(hours=1))

        report = await _report(session, permissions, desk, actor)
        assert len(report.sla) == 2
        by_id = {row.policy_id: row for row in report.sla}
        assert by_id[response.id].met == 1
        assert by_id[resolution.id].overdue == 1

    async def test_a_policy_with_no_clocks_in_the_window_is_left_out(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """0/0/0/0 줄을 만들지 않는다. 셀 것이 없는 정책 줄은 "지켰다" 로도
        "놓쳤다" 로도 안 읽히고, 화면만 길어진다."""
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        await desk.policy(session)

        assert (await _report(session, permissions, desk, actor)).sla == []


class TestWhatIsInTheWindow:
    async def test_a_clock_on_a_ticket_made_before_the_window_is_left_out(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        old = await desk.ticket(session, created_at=WINDOW_FROM - timedelta(days=1))
        await desk.clock(session, old, policy, target_at=NOW - timedelta(hours=1))

        report = await _report(session, permissions, desk, actor)
        assert report.sla == []
        assert report.tickets == 0

    async def test_only_tickets_are_counted(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**같은 프로젝트의 그냥 이슈는 안 센다.**

        데스크 프로젝트에도 내부 작업 이슈가 산다. 그것까지 세면 "티켓 40건"
        이 티켓 수가 아니게 되고, 상담원 성과가 실제보다 좋아 보인다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        await desk.ticket(session)
        await desk.ticket(session, is_ticket=False)

        report = await _report(session, permissions, desk, actor)
        assert report.tickets == 1, "티켓이 아닌 이슈가 섞였다"

    async def test_archived_tickets_are_left_out(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """접힌 티켓은 **SLA 셈에서도** 빠진다.

        두 곳을 같이 본다: 담당자별 셈과 SLA 셈은 서로 다른 질의라, 한쪽만
        접힌 것을 빼면 "티켓 1건, 위반 2건" 같은 답이 나온다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session)
        await desk.ticket(session)
        gone = await desk.ticket(session, archived=True)
        await desk.clock(session, gone, policy, target_at=NOW - timedelta(hours=1))

        report = await _report(session, permissions, desk, actor)
        assert report.tickets == 1
        assert report.sla == [], "접힌 티켓의 시계가 위반으로 셌다"

    async def test_another_projects_tickets_are_left_out(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        other = await _desk(session)
        _, actor = await _agent(session, desk)
        await desk.ticket(session)
        await other.ticket(session)

        assert (await _report(session, permissions, desk, actor)).tickets == 1


class TestAgentRows:
    async def test_the_agent_rows_add_up_to_the_total(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**담당자별 합이 창 안 티켓 수와 같다.**

        따로 세면 두 값이 어긋날 수 있고, 그때 어느 쪽이 진실인지 말할 수
        없다. 그래서 총계를 담당자별 합으로 만든다.
        """
        desk = await _desk(session)
        first, actor = await _agent(session, desk, name="일")
        second, _ = await _agent(session, desk, name="이")
        await desk.ticket(session, assignee=first)
        await desk.ticket(session, assignee=first)
        await desk.ticket(session, assignee=second)
        await desk.ticket(session)  # 담당자 없음

        report = await _report(session, permissions, desk, actor)
        assert report.tickets == 4
        assert sum(row.tickets for row in report.agents) == report.tickets

    async def test_unassigned_tickets_are_their_own_row(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """담당자 없는 티켓은 **빈 칸이 아니라 한 칸이다.**

        빼면 담당자별 합이 총계와 어긋나고, 하필 가장 봐야 할 무리 — 아무도
        안 잡은 티켓들 — 이 리포트에서 사라진다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        await desk.ticket(session)
        await desk.ticket(session)

        report = await _report(session, permissions, desk, actor)
        unassigned = [row for row in report.agents if row.assignee_id is None]
        assert len(unassigned) == 1
        assert unassigned[0].tickets == 2

    async def test_an_agent_who_resolved_nothing_has_no_average(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**`0` 이 아니라 `None` 이다.** `0` 은 "즉시 해결" 로 읽힌다 —
        하나도 못 끝낸 사람이 가장 빠른 사람으로 보인다."""
        desk = await _desk(session)
        agent, actor = await _agent(session, desk)
        await desk.ticket(session, assignee=agent)

        row = _row_for(await _report(session, permissions, desk, actor), agent.id)
        assert row.resolved == 0
        assert row.average_wallclock_seconds is None, "안 끝난 것이 0초로 셌다"

    async def test_the_average_covers_only_what_was_resolved(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """평균은 **끝난 것만으로** 정의된다.

        안 끝난 것을 0 으로 넣으면 평균이 내려가고, 창 끝 시각으로 넣으면
        올라간다 — 어느 쪽이든 "평균 2시간" 이 뜻하는 모집단이 바뀐다. 그래서
        `tickets`·`resolved` 를 같이 준다: 몇 건 중 몇 건인지 말하지 않는
        평균은 전체를 뜻하는 것처럼 읽힌다.
        """
        desk = await _desk(session)
        agent, actor = await _agent(session, desk)
        await desk.ticket(session, assignee=agent, resolved_at=INSIDE + timedelta(hours=1))
        await desk.ticket(session, assignee=agent, resolved_at=INSIDE + timedelta(hours=3))
        await desk.ticket(session, assignee=agent)  # 아직 안 끝났다

        row = _row_for(await _report(session, permissions, desk, actor), agent.id)
        assert row.tickets == 3
        assert row.resolved == 2
        assert row.average_wallclock_seconds == 2 * HOUR

    async def test_the_average_is_wallclock_not_business_hours(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """주말을 건너뛰지 않는다 — **벽시계다.**

        SLA 는 업무 달력으로 재므로 이 값과 SLA 목표는 다른 축이다. 이름에
        `wallclock` 이 박힌 이유이고, 업무 시간으로 재기 시작하면 그 이름이
        거짓이 된다.
        """
        desk = await _desk(session)
        agent, actor = await _agent(session, desk)
        # 금요일 저녁에 만들어 월요일 아침에 끝냈다. 업무 시간으로 재면 몇
        # 시간이지만 벽시계로는 사흘에 가깝다.
        friday = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
        monday = datetime(2026, 9, 7, 9, 0, tzinfo=UTC)
        await desk.ticket(session, created_at=friday, assignee=agent, resolved_at=monday)

        row = _row_for(await _report(session, permissions, desk, actor), agent.id)
        assert row.average_wallclock_seconds == int((monday - friday).total_seconds())


class TestVerdictsDoNotMove:
    async def test_editing_the_policy_does_not_change_a_past_verdict(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**리포트는 되계산하지 않는다.**

        `target_at` 을 저장한 이유가 이것이다(모델 주석). 리포트가 정책의 목표
        시간으로 다시 계산하면, 목표를 늘리는 것만으로 지난 달의 위반이
        사라진다 — 자기가 평가받는 숫자를 자기가 고치는 자리가 된다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)
        policy = await desk.policy(session, seconds=HOUR)
        issue = await desk.ticket(session)
        await desk.clock(
            session,
            issue,
            policy,
            target_at=INSIDE + timedelta(hours=1),
            completed_at=INSIDE + timedelta(hours=2),
        )
        before = (await _report(session, permissions, desk, actor)).sla[0]
        assert before.missed == 1

        # 목표를 열 배로 늘린다. 시계의 `target_at` 은 그대로다.
        policy.goals = [{"seconds": 10 * HOUR}]
        await session.flush()

        after = (await _report(session, permissions, desk, actor)).sla[0]
        assert after.missed == 1, "목표를 늘리니 지난 위반이 사라졌다"


class TestPermission:
    async def test_a_stranger_cannot_read_the_report(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        nobody = User(email=f"nb-{new_id()}@example.com", display_name="아무나", status="active")
        session.add(nobody)
        await session.flush()

        with pytest.raises(PermissionDeniedError):
            await _report(session, permissions, desk, actor_for(nobody))

    async def test_a_grant_on_another_project_does_not_open_this_one(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """프로젝트 단위 권한이다. 한 프로젝트의 상담원이 다른 프로젝트의
        성과를 볼 수 있으면 그건 권한이 아니라 장식이다."""
        desk = await _desk(session)
        other = await _desk(session)
        _, actor = await _agent(session, other)

        with pytest.raises(PermissionDeniedError):
            await _report(session, permissions, desk, actor)


class TestWindow:
    async def test_a_backwards_window_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """거꾸로 된 창은 **조건에 안 맞는 것이 아니라 물어볼 수 없는 것**이다.

        그대로 돌리면 빈 리포트가 나오고, 그건 "이 기간엔 티켓이 없었다" 로
        읽힌다.
        """
        desk = await _desk(session)
        _, actor = await _agent(session, desk)

        with pytest.raises(ValidationError) as exc:
            await DeskReportService(session, permissions).report(
                actor,
                project_id=desk.project.id,
                starts_at=WINDOW_TO,
                ends_at=WINDOW_FROM,
                now=NOW,
            )
        assert exc.value.code == "desk.report_window_invalid"

    async def test_too_wide_a_window_is_refused(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        desk = await _desk(session)
        _, actor = await _agent(session, desk)

        with pytest.raises(ValidationError) as exc:
            await DeskReportService(session, permissions).report(
                actor,
                project_id=desk.project.id,
                starts_at=WINDOW_FROM,
                ends_at=WINDOW_FROM + timedelta(days=MAX_DAYS),
                now=NOW,
            )
        assert exc.value.code == "desk.report_window_too_wide"
        assert exc.value.details == {"max_days": MAX_DAYS}

    async def test_the_widest_allowed_window_goes_through(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """상한 **바로 위**만 막는다. 경계에서 한 칸 어긋나면 "1년" 을 물어본
        사람이 이유를 못 듣고 거절당한다."""
        desk = await _desk(session)
        _, actor = await _agent(session, desk)

        report = await DeskReportService(session, permissions).report(
            actor,
            project_id=desk.project.id,
            starts_at=WINDOW_FROM,
            ends_at=WINDOW_FROM + timedelta(days=MAX_DAYS - 1),
            now=NOW,
        )
        assert report.tickets == 0


def _row_for(report: DeskReport, assignee_id: UUID) -> AgentRow:
    for row in report.agents:
        if row.assignee_id == assignee_id:
            return row
    raise AssertionError(f"{assignee_id} 줄이 리포트에 없다")
