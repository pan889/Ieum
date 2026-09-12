"""SLA 클럭이 실제로 움직이는지 (feature-map C4·C5).

`calendar.py`·`sla.py` 는 순수 함수라 손으로 계산해 시험했다. 여기는 **행을
쓰는 층**이다: 클럭이 걸리고, 멈추고, 끝나고, 위반이 잡히는지 실제 DB 로 본다.

붙잡는 것:

- `started_at` 은 **티켓이 만들어진 시각**이다. 지금으로 두면 아웃박스가 늦게
  훑은 만큼 모든 티켓이 공짜 시간을 얻는다.
- **내부 노트는 첫 응답이 아니다.** 고객이 볼 수 없는 글로 SLA 를 지킬 수
  있게 두면 그 지표는 아무 것도 뜻하지 않는다.
- **고객 자신의 회신도 응답이 아니다.** 고객이 한 번 더 물어서 우리 SLA 가
  지켜지는 것은 거꾸로다.
- **같은 이벤트를 두 번 받아도 목표가 다시 계산되지 않는다.**
- **멈춰 있는 클럭은 위반으로 잡히지 않는다.** 고객 답변을 기다리는 티켓이
  저절로 위반되면 그건 아무도 잘못하지 않은 위반이다.
- **에스컬레이션은 한 번만 실행된다.** 스윕은 15초마다 돈다 — 표시를 안 남기면
  담당자는 15초마다 호출된다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ieum.core.ids import new_id
from ieum.core.permissions import PermissionService, set_permission_service
from ieum.modules.desk import clock as desk_clock
from ieum.modules.desk.models import BusinessCalendarRow, SlaClock, SlaPolicy, TicketExt
from ieum.modules.identity.models import User
from ieum.modules.issues.models import Issue, IssueType, Workflow, WorkflowState
from ieum.modules.org.models import Project
from ieum.modules.org.repository import OrgPermissionResolver

HOUR = 3600

#: 항상 열려 있는 달력. 클럭의 **배선**을 보는 시험이므로 업무 시간 계산을
#: 끼워 넣지 않는다 — 그건 `test_desk_calendar.py` 가 이미 26개로 본다.
ALWAYS_OPEN = {str(day): [["00:00", "23:59"]] for day in range(7)}


@pytest.fixture
def permissions() -> PermissionService:
    service = PermissionService(resolver=OrgPermissionResolver(), step_up_window_seconds=300)
    set_permission_service(service)
    return service


async def _calendar(
    session: AsyncSession, hours: dict[str, object] | None = None
) -> BusinessCalendarRow:
    row = BusinessCalendarRow(
        name=f"cal-{new_id()}",
        timezone="Asia/Seoul",
        working_hours=hours if hours is not None else ALWAYS_OPEN,
        holidays=[],
    )
    session.add(row)
    await session.flush()
    return row


async def _ticket(
    session: AsyncSession, *, created_at: datetime | None = None, priority: int = 3
) -> tuple[Project, Issue, User]:
    """프로젝트·이슈·티켓 확장 한 벌. 포털을 거치지 않는다 — 이 시험이 보는
    것은 클럭이고, 포털 흐름은 다른 파일이 본다."""
    project = Project(key=f"S{new_id().hex[-6:].upper()}", name="SLA")
    session.add(project)
    workflow = Workflow(name=f"wf-{new_id()}")
    session.add(workflow)
    await session.flush()
    open_state = WorkflowState(
        workflow_id=workflow.id, name="Open", category="todo", position=0, is_initial=True
    )
    session.add(open_state)
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
        state_id=open_state.id,
        key_seq=1,
        summary="프린터가 안 됩니다",
        priority=priority,
    )
    session.add(issue)
    await session.flush()
    if created_at is not None:
        issue.created_at = created_at
        await session.flush()
    session.add(TicketExt(issue_id=issue.id, reporter_customer_id=customer.id, channel="portal"))
    await session.flush()
    return project, issue, customer


async def _policy(
    session: AsyncSession,
    project: Project,
    *,
    metric: str = "first_response",
    seconds: int = 4 * HOUR,
    calendar: BusinessCalendarRow | None = None,
    pause_state_ids: list[object] | None = None,
    escalations: list[dict[str, object]] | None = None,
) -> SlaPolicy:
    row = SlaPolicy(
        project_id=project.id,
        name=f"policy-{new_id()}",
        metric=metric,
        calendar_id=(calendar or await _calendar(session)).id,
        goals=[{"seconds": seconds}],
        pause_state_ids=pause_state_ids or [],
        escalations=escalations or [],
    )
    session.add(row)
    await session.flush()
    return row


async def _clock(session: AsyncSession, issue: Issue, policy: SlaPolicy) -> SlaClock | None:
    return await session.get(SlaClock, (issue.id, policy.id))


class TestStartingTheClock:
    async def test_it_starts_from_when_the_ticket_was_made(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**지금이 아니라 티켓이 만들어진 시각이다.**

        지금으로 두면 아웃박스가 5분 밀린 날에는 모든 티켓이 5분씩 유리해진다.
        """
        made = datetime(2026, 9, 7, 1, 0, tzinfo=UTC)
        project, issue, _ = await _ticket(session, created_at=made)
        policy = await _policy(session, project, seconds=2 * HOUR)

        assert await desk_clock.start_clocks(session, issue.id) == 1
        row = await _clock(session, issue, policy)
        assert row is not None
        assert row.started_at.astimezone(UTC) == made
        # 항상 열린 달력이라 목표는 정확히 2시간 뒤다.
        assert row.target_at.astimezone(UTC) == made + timedelta(hours=2)

    async def test_a_second_event_does_not_recompute_the_target(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """아웃박스가 같은 이벤트를 두 번 줄 수 있다. 목표가 다시 계산되면
        재시도한 날의 티켓만 목표가 달라진다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        first = await _clock(session, issue, policy)
        assert first is not None
        target = first.target_at

        assert await desk_clock.start_clocks(session, issue.id) == 0
        again = await _clock(session, issue, policy)
        assert again is not None
        assert again.target_at == target

    async def test_a_disabled_policy_does_not_get_a_clock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        policy.is_enabled = False
        await session.flush()
        assert await desk_clock.start_clocks(session, issue.id) == 0

    async def test_a_plain_issue_gets_no_clock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓이 아닌 이슈에는 SLA 가 없다 — 고객에게 한 약속이 아니다."""
        project, issue, _ = await _ticket(session)
        await _policy(session, project)
        await session.delete(await session.get(TicketExt, issue.id))
        await session.flush()
        assert await desk_clock.start_clocks(session, issue.id) == 0

    async def test_a_broken_calendar_does_not_kill_the_worker(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**달력 하나가 망가졌다고 다른 프로젝트의 클럭까지 멈추면 피해가
        번진다.** 저장할 때 검증하므로 여기 오는 것은 손으로 고친 행이다."""
        project, issue, _ = await _ticket(session)
        broken = await _calendar(session, hours={"0": "월요일 아침부터"})
        await _policy(session, project, calendar=broken)
        # 예외가 아니라 0 이다.
        assert await desk_clock.start_clocks(session, issue.id) == 0


class TestFinishingTheFirstResponse:
    async def test_a_public_reply_from_an_agent_completes_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)

        agent = await _person(session)
        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
            == 1
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is not None

    async def test_an_internal_note_does_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**고객이 볼 수 없는 글로 SLA 를 지킬 수 있게 두면 그 지표는 아무
        것도 뜻하지 않는다.**"""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)

        agent = await _person(session)
        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=True) == 0
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None

    async def test_the_customers_own_reply_does_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객이 한 번 더 물어서 우리 SLA 가 지켜지는 것은 거꾸로다."""
        project, issue, customer = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)

        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=customer.id, is_internal=False)
            == 0
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None

    async def test_a_second_reply_does_not_move_the_first_response(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """갱신되면 그 지표는 "마지막 응답" 이 된다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="first_response")
        await desk_clock.start_clocks(session, issue.id)
        agent = await _person(session)
        await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
        row = await _clock(session, issue, policy)
        assert row is not None
        first = row.completed_at

        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
            == 0
        )
        assert row.completed_at == first

    async def test_a_reply_does_not_complete_the_resolution_clock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """회신은 해결이 아니다. 두 지표를 한 이벤트로 끝내면 해결 SLA 가
        의미를 잃는다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="resolution")
        await desk_clock.start_clocks(session, issue.id)
        agent = await _person(session)
        assert (
            await desk_clock.on_comment(session, issue.id, actor_id=agent.id, is_internal=False)
            == 0
        )
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None


class TestFinishingTheResolution:
    async def test_moving_to_done_completes_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="resolution")
        await desk_clock.start_clocks(session, issue.id)
        assert await desk_clock.on_transition(session, issue.id, to_state_category="done") >= 1
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is not None

    async def test_moving_within_open_states_does_not(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, metric="resolution")
        await desk_clock.start_clocks(session, issue.id)
        await desk_clock.on_transition(session, issue.id, to_state_category="in_progress")
        row = await _clock(session, issue, policy)
        assert row is not None and row.completed_at is None


class TestTheSweepsLockWhatTheyTake:
    """**워커가 둘이면 같은 클럭을 둘이 집었다.**

    HA 가이드는 워커를 여러 개 띄워도 된다고 적어 두었다. 그런데 위반·
    에스컬레이션 스윕만 행을 안 잠가서, 두 스윕이 같은 클럭을 같이 읽고 둘 다
    `breached_at` 을 적고 둘 다 돌려줬다 — 위반 알림이 두 통 가고
    에스컬레이션이 두 번 돈다. 아웃박스는 처음부터 잠그고 있었다
    (`fetch_unpublished`).

    **여기서 보는 것은 질의의 모양이다.** 진짜 경쟁을 재현하려면 커밋된 두
    트랜잭션이 필요한데 이 시험 세션은 하나로 굴러간다. 그래도 `FOR UPDATE`
    를 지우면 이 시험이 붉어진다 — 막으려는 것이 바로 그 삭제다.
    """

    def _sql(self, statement: Any) -> str:
        """**Postgres 방언으로 찍어야 한다.** `SKIP LOCKED` 는 방언이 붙여 주는
        것이라, 기본 방언으로 찍으면 `FOR UPDATE` 까지만 나온다."""
        from sqlalchemy.dialects import postgresql

        return str(statement.compile(dialect=postgresql.dialect())).upper()

    async def test_the_breach_sweep_asks_for_a_row_lock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        captured: list[object] = []
        real = session.execute

        async def spy(statement: object, *args: object, **kwargs: object) -> object:
            captured.append(statement)
            return await real(statement, *args, **kwargs)  # type: ignore[arg-type]

        session.execute = spy  # type: ignore[method-assign]
        try:
            await desk_clock.sweep_breaches(session)
        finally:
            session.execute = real  # type: ignore[method-assign]

        sql = self._sql(captured[0])
        assert "FOR UPDATE" in sql
        assert "SKIP LOCKED" in sql

    async def test_the_escalation_sweep_asks_for_a_row_lock(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        captured: list[object] = []
        real = session.execute

        async def spy(statement: object, *args: object, **kwargs: object) -> object:
            captured.append(statement)
            return await real(statement, *args, **kwargs)  # type: ignore[arg-type]

        session.execute = spy  # type: ignore[method-assign]
        try:
            await desk_clock.sweep_escalations(session)
        finally:
            session.execute = real  # type: ignore[method-assign]

        sql = self._sql(captured[0])
        assert "FOR UPDATE" in sql
        assert "SKIP LOCKED" in sql
        # **정책은 안 잠근다.** 여러 클럭이 함께 보는 행이라 같이 잠그면
        # 스윕끼리 서로를 막는다.
        assert "OF SLA_CLOCK" in sql


class TestSweepingForBreaches:
    async def test_it_finds_a_clock_past_its_target(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.flush()

        found = await desk_clock.sweep_breaches(session)
        assert [c.issue_id for c in found] == [issue.id]
        assert row.breached_at is not None

    async def test_it_does_not_report_the_same_breach_twice(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`breached_at` 은 "위반인가" 가 아니라 "알렸는가" 다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(minutes=1)
        await session.flush()

        assert len(await desk_clock.sweep_breaches(session)) == 1
        assert await desk_clock.sweep_breaches(session) == []

    async def test_a_paused_clock_is_not_a_breach(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 클래스의 이유다.** 고객 답변을 기다리는 티켓이
        저절로 위반되면 그건 아무도 잘못하지 않은 위반이다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(minutes=1)
        row.paused_at = datetime.now(UTC) - timedelta(hours=2)
        await session.flush()

        assert await desk_clock.sweep_breaches(session) == []

    async def test_a_completed_clock_is_not_a_breach(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이미 지킨 약속이 나중에 위반으로 잡히면 지표가 뒤집힌다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(hours=2)
        row.completed_at = datetime.now(UTC) - timedelta(hours=3)
        await session.flush()

        assert await desk_clock.sweep_breaches(session) == []


class TestPausing:
    async def test_it_pauses_in_a_named_state_and_resumes_out_of_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """일시정지는 **상태 id** 로 정한다 — 이름도 category 도 아니다."""
        project, issue, _ = await _ticket(session)
        state_id = issue.state_id
        policy = await _policy(session, project, pause_state_ids=[state_id])
        await desk_clock.start_clocks(session, issue.id)

        # 이 상태로 들어오면 멈춘다.
        await desk_clock.on_transition(session, issue.id, to_state_category="todo")
        row = await _clock(session, issue, policy)
        assert row is not None and row.paused_at is not None

        # 다른 상태로 나가면 다시 돈다. 상태를 실제로 바꿔 준다.
        other = WorkflowState(
            workflow_id=(await session.get(Workflow, (await _workflow_id(session, issue)))).id,
            name="In Progress",
            category="in_progress",
            position=1,
            is_initial=False,
        )
        session.add(other)
        await session.flush()
        issue.state_id = other.id
        await session.flush()

        await desk_clock.on_transition(session, issue.id, to_state_category="in_progress")
        assert row.paused_at is None
        # 기다린 만큼이 기록된다.
        assert row.paused_seconds >= 0


async def _person(session: AsyncSession) -> User:
    row = User(email=f"a-{new_id()}@example.com", display_name="상담원", status="active")
    session.add(row)
    await session.flush()
    return row


async def _workflow_id(session: AsyncSession, issue: Issue) -> object:
    issue_type = await session.get(IssueType, issue.type_id)
    assert issue_type is not None
    return issue_type.workflow_id


async def _count_clocks(session: AsyncSession) -> int:
    return len(list((await session.execute(select(SlaClock))).scalars().all()))


class TestEscalation:
    """조건을 지난 규칙을 실행한다 (C5).

    **이 층이 붙잡는 것은 "한 번만" 이다.** 규칙을 고르는 계산은
    `test_desk_sla.py` 가 순수 함수로 이미 본다 — 여기는 표시가 실제로
    저장되는지, 그래서 다음 스윕이 같은 규칙을 다시 돌리지 않는지를 본다.
    """

    async def _overdue(
        self,
        session: AsyncSession,
        escalations: list[dict[str, object]],
        *,
        consumed_ratio: float = 1.5,
    ) -> tuple[Issue, SlaPolicy, SlaClock]:
        """목표를 `consumed_ratio` 배 쓴 클럭 하나. 달력은 항상 열려 있다.

        시계를 기다리지 않고 목표 시각을 당긴다 — 소비율은 남은 시간에서
        거꾸로 나오므로 목표 시각을 옮기는 것으로 만들 수 있다.
        """
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, escalations=escalations)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        goal = row.goal_seconds
        row.target_at = datetime.now(UTC) - timedelta(seconds=goal * (consumed_ratio - 1))
        await session.flush()
        return issue, policy, row

    async def test_the_clock_records_the_promise_it_was_given(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """`goal_seconds` 가 없으면 "목표의 몇 %" 를 잴 수 없다. `target_at`
        에서 거꾸로 계산하려면 달력이 필요하고, 멈춤으로 목표가 밀린 뒤에는
        원래 약속이 얼마였는지 알 수 없다."""
        project, issue, _ = await _ticket(session)
        policy = await _policy(session, project, seconds=4 * HOUR)
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        assert row.goal_seconds == 4 * HOUR

    async def test_a_passed_rule_runs(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        who = await _person(session)
        _, _, clock = await self._overdue(
            session, [{"at_percent": 100, "action": "notify", "user_id": str(who.id)}]
        )
        done = await desk_clock.sweep_escalations(session)
        assert [item.rule.key for item in done] == ["100:notify"]
        # 실제로 몇 %에서 돌았는가. 규칙의 조건이 아니다.
        assert done[0].at_percent >= 100
        assert clock.escalated == ["100:notify"]

    async def test_it_runs_only_once(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """**이 시험이 이 클래스의 이유다.** 스윕은 15초마다 돈다."""
        who = await _person(session)
        await self._overdue(
            session, [{"at_percent": 100, "action": "notify", "user_id": str(who.id)}]
        )
        assert len(await desk_clock.sweep_escalations(session)) == 1
        assert await desk_clock.sweep_escalations(session) == []

    async def test_a_rule_that_has_not_come_yet_does_not_run(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        who = await _person(session)
        await self._overdue(
            session,
            [{"at_percent": 100, "action": "notify", "user_id": str(who.id)}],
            consumed_ratio=0.1,
        )
        assert await desk_clock.sweep_escalations(session) == []

    async def test_a_paused_clock_is_not_escalated(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """고객 답변을 기다리는 동안 %가 올라 담당자가 호출되면, 우리가 안 한
        일이 아닌데 부르는 것이다."""
        who = await _person(session)
        _, _, clock = await self._overdue(
            session, [{"at_percent": 100, "action": "notify", "user_id": str(who.id)}]
        )
        clock.paused_at = datetime.now(UTC)
        await session.flush()
        assert await desk_clock.sweep_escalations(session) == []

    async def test_a_completed_clock_is_not_escalated(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """이미 응답한 티켓에 "응답이 늦습니다" 가 가면 그 알림은 다음부터
        무시된다."""
        who = await _person(session)
        _, _, clock = await self._overdue(
            session, [{"at_percent": 100, "action": "notify", "user_id": str(who.id)}]
        )
        clock.completed_at = datetime.now(UTC)
        await session.flush()
        assert await desk_clock.sweep_escalations(session) == []

    async def test_raise_priority_actually_raises_it(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        issue, _, _ = await self._overdue(
            session, [{"at_percent": 100, "action": "raise_priority", "priority": 5}]
        )
        assert len(await desk_clock.sweep_escalations(session)) == 1
        await session.refresh(issue)
        assert issue.priority == 5

    async def test_it_never_lowers_a_priority_someone_raised(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """규칙은 "적어도 이만큼" 을 뜻한다. 사람이 더 높게 올려 둔 티켓을
        규칙이 끌어내리면 그 판단이 조용히 뒤집힌다."""
        project, issue, _ = await _ticket(session, priority=5)
        policy = await _policy(
            session,
            project,
            escalations=[{"at_percent": 100, "action": "raise_priority", "priority": 4}],
        )
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(hours=1)
        await session.flush()

        await desk_clock.sweep_escalations(session)
        await session.refresh(issue)
        assert issue.priority == 5

    async def test_raising_the_priority_leaves_a_trace(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """안 남기면 담당자는 자기가 안 만진 값이 바뀐 것을 보고 이유를 찾을
        수 없다. `actor_id` 는 비운다 — 사람이 한 일이 아니다."""
        from ieum.modules.issues.models import IssueHistory

        project, issue, _ = await _ticket(session, priority=2)
        policy = await _policy(
            session,
            project,
            escalations=[{"at_percent": 100, "action": "raise_priority", "priority": 5}],
        )
        await desk_clock.start_clocks(session, issue.id)
        row = await _clock(session, issue, policy)
        assert row is not None
        row.target_at = datetime.now(UTC) - timedelta(hours=1)
        await session.flush()
        await desk_clock.sweep_escalations(session)

        entries = list(
            (await session.execute(select(IssueHistory).where(IssueHistory.issue_id == issue.id)))
            .scalars()
            .all()
        )
        changes = [c for entry in entries for c in entry.changes if c.get("field") == "priority"]
        assert changes == [{"field": "priority", "from": "2", "to": "5"}]
        assert all(entry.actor_id is None for entry in entries)

    async def test_two_passed_rules_both_run_in_one_sweep(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """워커가 한동안 멈춰 있었으면 두 조건을 같은 주기에 지나친다. 하나만
        하고 나머지를 버리면 우선순위를 올리는 규칙이 사라진다."""
        who = await _person(session)
        await self._overdue(
            session,
            [
                {"at_percent": 50, "action": "notify", "user_id": str(who.id)},
                {"at_percent": 100, "action": "raise_priority", "priority": 5},
            ],
            consumed_ratio=2.0,
        )
        done = await desk_clock.sweep_escalations(session)
        assert [item.rule.at_percent for item in done] == [50, 100]

    async def test_a_policy_without_rules_is_not_even_looked_at(
        self, session: AsyncSession, permissions: PermissionService
    ) -> None:
        """티켓이 쌓이면 이 조건이 없는 스윕은 클럭 전체를 읽고 달력을
        파싱한다."""
        await self._overdue(session, [])
        assert await desk_clock.sweep_escalations(session) == []
